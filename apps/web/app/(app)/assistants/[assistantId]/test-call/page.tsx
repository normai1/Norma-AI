"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useTenant } from "@/components/app/tenant-provider";
import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
} from "@/components/organizations/ui";
import { fetchTestCallTicket, getAssistant, type Assistant } from "@/lib/assistants";
import {
  ECHO_GATE_IDLE,
  type EchoGateState,
  floatToPCM16,
  interpretCloseCode,
  nextEchoGate,
  pcm16ToFloat32,
  resampleLinear,
  rms,
} from "@/lib/audio";

const VOICE_WS_URL = process.env.NEXT_PUBLIC_VOICE_WS_URL ?? "ws://localhost:8080";

/**
 * Identifies which build of this page a session is actually running, sent to
 * the voice worker so it appears in that session's server logs.
 *
 * A browser serving a cached bundle is indistinguishable, from the server
 * side, from a fix that did not work - and several rounds of barge-in
 * debugging were spent unable to tell those apart. Bump this whenever the
 * audio path here changes.
 */
const CLIENT_BUILD = "v5-direct-audio";

/**
 * Whether to gate the microphone while the assistant is speaking.
 *
 * Off by default. It was built to stop the assistant hearing itself on a
 * speaker setup, but its threshold is a multiple of the *peak* echo it
 * learns, and with loud speakers that bar can land above the caller's own
 * voice - locking them out for the whole reply, which is precisely when an
 * interruption needs to get through. Measured in a real session: audio
 * arriving at the server sat at 0.02-0.06 of full scale throughout, while
 * the caller was speaking normally.
 *
 * Set NEXT_PUBLIC_ECHO_GATE=on to re-enable it for a setup where the
 * feedback loop is the bigger problem.
 */
const ECHO_GATE_ENABLED = process.env.NEXT_PUBLIC_ECHO_GATE === "on";
const TARGET_SAMPLE_RATE = 16000;

type CallStatus =
  | "idle"
  | "loading-ticket"
  | "requesting-mic"
  | "mic-denied"
  | "mic-unsupported"
  | "connecting"
  | "connected"
  | "ended"
  | "ticket-error"
  | "auth-error";

const STATUS_LABEL: Record<CallStatus, string> = {
  idle: "Not started",
  "loading-ticket": "Preparing...",
  "requesting-mic": "Waiting for microphone permission...",
  "mic-denied": "Microphone access denied",
  "mic-unsupported": "Not supported in this browser",
  connecting: "Connecting...",
  connected: "Connected",
  ended: "Call ended",
  "ticket-error": "Couldn't start the test call",
  "auth-error": "Not authorized",
};

// Deliberately excludes "mic-unsupported" - once the browser has been found
// unsupported, offering "Start test call" again would just fail the same way.
const _RESTARTABLE = new Set<CallStatus>([
  "idle",
  "ended",
  "ticket-error",
  "auth-error",
  "mic-denied",
]);

interface TranscriptLine {
  id: number;
  speaker: "caller" | "assistant";
  text: string;
  /**
   * Whether this line is finished and must never be appended to again. The
   * transcript updaters below are pure functions of the previous lines - they
   * decide "extend the last line" vs. "start a new one" from this flag rather
   * than from a ref mutated inside the updater. React double-invokes state
   * updaters in development, so a ref mutated in there made the second pass
   * take a different branch and append a duplicate line - the real cause of
   * one spoken sentence showing up twice.
   */
  closed: boolean;
}

/** Pure: next id from the existing lines, never a mutable counter. */
function nextLineId(lines: TranscriptLine[]): number {
  return lines.reduce((highest, line) => Math.max(highest, line.id), -1) + 1;
}

/**
 * Index of that speaker's most recent still-open line, or -1. Deliberately
 * not just "the last line": the caller's line stays open across their turn
 * while the assistant's reply can be appended after it, and a turn_ended for
 * one turn can arrive after the next turn's transcripts have started. Looking
 * only at the trailing line made those cases append a duplicate.
 */
function lastOpenIndex(
  lines: TranscriptLine[],
  speaker: TranscriptLine["speaker"],
): number {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (lines[index].speaker === speaker && !lines[index].closed) {
      return index;
    }
  }

  return -1;
}

/** Index of that speaker's most recent line regardless of state, or -1. */
function lastIndexFor(
  lines: TranscriptLine[],
  speaker: TranscriptLine["speaker"],
): number {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (lines[index].speaker === speaker) {
      return index;
    }
  }

  return -1;
}

function replaceAt(
  lines: TranscriptLine[],
  target: number,
  changes: Partial<TranscriptLine>,
): TranscriptLine[] {
  return lines.map((line, index) => (index === target ? { ...line, ...changes } : line));
}

function appendLine(
  lines: TranscriptLine[],
  speaker: TranscriptLine["speaker"],
  text: string,
  closed: boolean,
): TranscriptLine[] {
  return [...lines, { id: nextLineId(lines), speaker, text, closed }];
}

type ServerMessage =
  | { type: "transcript"; text: string; is_final: boolean }
  | { type: "turn_ended"; text: string }
  | { type: "caller_speech_started" }
  | { type: "playback_cancelled" }
  | { type: "llm_delta"; text: string }
  | { type: "llm_complete"; text: string }
  | { type: "llm_error"; text: string }
  | { type: "tts_error"; text: string }
  | { type: "reply_finished" }
  | { type: "session_failover"; reason: string; message: string };

export default function TestCallPage() {
  const params = useParams<{ assistantId: string }>();
  const assistantId = params.assistantId;

  const { status: tenantStatus, error: tenantError, activeWorkspace } = useTenant();

  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [callStatus, setCallStatus] = useState<CallStatus>("idle");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [inlineNotice, setInlineNotice] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const playbackQueueRef = useRef<AudioBufferSourceNode[]>([]);
  const nextPlaybackTimeRef = useRef(0);
  /**
   * Set only while playback is successfully routed through playbackAudioRef's
   * element; null means "play straight to context.destination instead". The
   * AudioBufferSourceNode graph and its precise gapless scheduling are
   * identical either way - this only changes where that graph's output goes.
   */
  const playbackDestinationRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  /**
   * A real, DOM-rendered <audio> element (see the JSX below), used as the
   * playback sink so the browser's echo canceller on the mic stream has an
   * actual played reference to cancel against - which it does not get from
   * raw context.destination output, leaving the assistant's own voice to
   * bleed into the mic on speaker setups and keep VAD reading "caller
   * speaking" continuously, defeating barge-in.
   *
   * It must genuinely be in the DOM: a first attempt at this used a detached
   * `new Audio()`, which Chrome accepted without complaint and then played
   * nothing at all, silencing every reply with no error anywhere. Hence both
   * that element and the fallback below - echo cancellation is worth having,
   * but never at the risk of a call the caller cannot hear.
   */
  const playbackAudioRef = useRef<HTMLAudioElement | null>(null);
  /**
   * Keeps the assistant from hearing itself: while its reply is playing out
   * of these speakers, microphone frames that are just that playback coming
   * back are replaced with silence, and only genuinely louder speech is
   * forwarded. See nextEchoGate for why this is the fix rather than anything
   * in the barge-in path.
   */
  const echoGateRef = useRef<EchoGateState>(ECHO_GATE_IDLE);

  const fetchAssistant = useCallback(async () => {
    if (!activeWorkspace) {
      return null;
    }

    return getAssistant(activeWorkspace.organization_id, activeWorkspace.id, assistantId);
  }, [activeWorkspace, assistantId]);

  useEffect(() => {
    let cancelled = false;

    fetchAssistant()
      .then((loaded) => {
        if (!cancelled && loaded) {
          setAssistant(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : "Could not load this assistant.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchAssistant]);

  const teardown = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;

    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;

    for (const track of micStreamRef.current?.getTracks() ?? []) {
      track.stop();
    }
    micStreamRef.current = null;

    for (const node of playbackQueueRef.current) {
      try {
        node.stop();
      } catch {
        // Already stopped or finished naturally.
      }
    }
    playbackQueueRef.current = [];

    // The element itself is owned by React (it is rendered in the JSX below),
    // so only its playback state is unwound here - never the ref.
    playbackAudioRef.current?.pause();
    if (playbackAudioRef.current) {
      playbackAudioRef.current.srcObject = null;
    }
    playbackDestinationRef.current = null;

    void audioContextRef.current?.close();
    audioContextRef.current = null;
    nextPlaybackTimeRef.current = 0;
  }, []);

  useEffect(() => () => teardown(), [teardown]);

  /**
   * Reports what the browser did, back to the server, so it lands in the
   * call's own log. The audio the caller actually hears is scheduled here,
   * not on the server, so without this the server cannot tell "I cancelled
   * and the browser stopped" from "I cancelled and the browser carried
   * on" - the exact ambiguity that made this bug so hard to place.
   */
  const reportToServer = useCallback((event: Record<string, unknown>) => {
    const ws = wsRef.current;

    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ source: "client", ...event }));
      } catch {
        // Diagnostics must never break a call.
      }
    }
  }, []);

  const flushPlayback = useCallback(
    (reason: string) => {
      const queued = playbackQueueRef.current.length;
      let stopped = 0;

      for (const node of playbackQueueRef.current) {
        try {
          node.stop();
          stopped += 1;
        } catch {
          // Already stopped or finished naturally.
        }
      }
      playbackQueueRef.current = [];

      const context = audioContextRef.current;
      // How much audio was still scheduled to play - the thing the caller
      // would have gone on hearing had this not run.
      const remaining = context
        ? Math.max(0, nextPlaybackTimeRef.current - context.currentTime)
        : 0;

      if (context) {
        nextPlaybackTimeRef.current = context.currentTime;
      }

      setSpeaking(false);

      reportToServer({
        event: "flush",
        reason,
        queued,
        stopped,
        remaining: Number(remaining.toFixed(2)),
      });
    },
    [reportToServer],
  );

  const playAudioChunk = useCallback((buffer: ArrayBuffer) => {
    const context = audioContextRef.current;

    if (!context) {
      return;
    }

    let samples = pcm16ToFloat32(buffer);

    if (context.sampleRate !== TARGET_SAMPLE_RATE) {
      samples = resampleLinear(samples, TARGET_SAMPLE_RATE, context.sampleRate);
    }

    if (samples.length === 0) {
      return;
    }

    const audioBuffer = context.createBuffer(1, samples.length, context.sampleRate);
    audioBuffer.copyToChannel(samples, 0);

    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);

    const startAt = Math.max(context.currentTime, nextPlaybackTimeRef.current);
    source.start(startAt);
    nextPlaybackTimeRef.current = startAt + audioBuffer.duration;

    playbackQueueRef.current.push(source);
    setSpeaking(true);

    source.onended = () => {
      playbackQueueRef.current = playbackQueueRef.current.filter((node) => node !== source);

      if (playbackQueueRef.current.length === 0) {
        setSpeaking(false);
      }
    };
  }, []);

  /**
   * ElevenLabs' commit_strategy=vad finalizes (is_final: true) on its own
   * internal pauses, a finer-grained boundary than Norma's own turn
   * boundary (Silero VAD + stop_secs/fallback) - a single continuous
   * utterance with a natural mid-sentence pause can arrive as several
   * separate is_final:true "transcript" messages. Every "transcript"
   * message therefore updates the same open caller line regardless of
   * is_final; only turn_ended (below), the authoritative Norma-level turn
   * boundary, closes it and readies a new one for the caller's next turn.
   */
  /**
   * STT and Norma's VAD-driven turn detection are independent async
   * pipelines, so a transcript for a turn that already ended can still
   * straggle in afterwards. Repeating the text of the caller line that was
   * just closed is exactly that straggler and is dropped; anything else is
   * genuinely new speech and opens a new line, so nothing the caller says is
   * ever silently discarded.
   */
  const setCallerLine = useCallback((text: string) => {
    setTranscript((lines) => {
      const open = lastOpenIndex(lines, "caller");

      if (open !== -1) {
        return replaceAt(lines, open, { text });
      }

      // No open caller line: either a straggler repeating the turn that just
      // closed (dropped), or genuinely new speech (starts a new line, so
      // nothing the caller says is ever silently discarded).
      const previous = lastIndexFor(lines, "caller");

      if (previous !== -1 && lines[previous].text === text) {
        return lines;
      }

      return appendLine(lines, "caller", text, false);
    });
  }, []);

  const finalizeCallerLine = useCallback((text: string) => {
    setTranscript((lines) => {
      const open = lastOpenIndex(lines, "caller");

      // Close the line, keeping whatever text the transcript stream last put
      // there - turn_ended can arrive after the next turn's transcripts have
      // begun, and its (older) text would otherwise overwrite newer words.
      if (open !== -1) {
        return replaceAt(lines, open, { closed: true });
      }

      const previous = lastIndexFor(lines, "caller");

      if (previous !== -1 && lines[previous].text === text) {
        return lines;
      }

      return appendLine(lines, "caller", text, true);
    });
  }, []);

  const appendAssistantDelta = useCallback((delta: string) => {
    setTranscript((lines) => {
      const open = lastOpenIndex(lines, "assistant");

      return open === -1
        ? appendLine(lines, "assistant", delta, false)
        : replaceAt(lines, open, { text: lines[open].text + delta });
    });
  }, []);

  const finalizeAssistantLine = useCallback((fullText: string) => {
    setTranscript((lines) => {
      const open = lastOpenIndex(lines, "assistant");

      return open === -1
        ? appendLine(lines, "assistant", fullText, true)
        : replaceAt(lines, open, { text: fullText, closed: true });
    });
  }, []);

  const handleServerMessage = useCallback(
    (event: MessageEvent<string | ArrayBuffer>) => {
      if (event.data instanceof ArrayBuffer) {
        playAudioChunk(event.data);
        return;
      }

      let message: ServerMessage;

      try {
        message = JSON.parse(event.data) as ServerMessage;
      } catch {
        return;
      }

      switch (message.type) {
        case "transcript":
          // ElevenLabs emits committed transcripts with empty text (confirmed
          // against the live API). Rendering one would blank out what the
          // caller actually said, which looked like "no transcript at all".
          if (message.text.trim()) {
            setCallerLine(message.text);
          }
          break;
        case "turn_ended":
          if (message.text.trim()) {
            finalizeCallerLine(message.text);
          }
          break;
        case "llm_delta":
          appendAssistantDelta(message.text);
          break;
        case "llm_complete":
          finalizeAssistantLine(message.text);
          break;
        case "caller_speech_started":
          flushPlayback("caller_speech_started");
          break;
        case "playback_cancelled":
          // The server abandoned the reply it was speaking. Audio arrives
          // ahead of playback and is scheduled locally, so without dropping
          // what is already queued here the assistant audibly carries on for
          // seconds after being interrupted - which is what made a
          // server-side barge-in look like it did nothing at all whenever
          // caller_speech_started above was not the thing that triggered it.
          flushPlayback("playback_cancelled");
          break;
        case "reply_finished":
          setSpeaking(false);
          break;
        case "llm_error":
        case "tts_error":
          setInlineNotice(message.text);
          break;
        case "session_failover":
          setInlineNotice(message.message);
          break;
        default:
          break;
      }
    },
    [
      playAudioChunk,
      setCallerLine,
      finalizeCallerLine,
      appendAssistantDelta,
      finalizeAssistantLine,
      flushPlayback,
    ],
  );

  const startCall = useCallback(async () => {
    if (!activeWorkspace) {
      return;
    }

    setInlineNotice(null);
    // Line identity is derived from this array itself, so clearing it is the
    // whole reset - there are no counters or partial-line refs to unwind.
    setTranscript([]);

    setCallStatus("loading-ticket");
    setStatusMessage(null);

    let ticket: string;

    try {
      const result = await fetchTestCallTicket(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
      );
      ticket = result.ticket;
    } catch (err) {
      setCallStatus("ticket-error");
      setStatusMessage(
        err instanceof Error ? err.message : "Could not start a test call.",
      );
      return;
    }

    if (
      typeof window === "undefined" ||
      !("AudioWorklet" in window) ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      setCallStatus("mic-unsupported");
      setStatusMessage("Your browser doesn't support in-browser test calls.");
      return;
    }

    setCallStatus("requesting-mic");

    let micStream: MediaStream;

    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        // Explicit rather than relying on browser defaults, and only half of
        // the defence: echo cancellation can only subtract audio the browser
        // sees as a played reference, which raw context.destination output is
        // not. playbackAudioRef's element is the other half - without it this
        // constraint has little to work with on a speaker setup, and the
        // assistant's own TTS bleeds into the mic, keeping VAD reading
        // "caller speaking" continuously and defeating barge-in.
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch {
      setCallStatus("mic-denied");
      setStatusMessage("Microphone access was denied. Allow microphone access and try again.");
      return;
    }

    micStreamRef.current = micStream;

    const context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    audioContextRef.current = context;
    nextPlaybackTimeRef.current = context.currentTime;

    await context.audioWorklet.addModule("/worklets/pcm-capture-processor.js");

    setCallStatus("connecting");

    const ws = new WebSocket(
      `${VOICE_WS_URL}/media/session?ticket=${encodeURIComponent(ticket)}` +
        `&client=${encodeURIComponent(CLIENT_BUILD)}`,
    );
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setCallStatus("connected");

      const source = context.createMediaStreamSource(micStream);
      const workletNode = new AudioWorkletNode(context, "pcm-capture-processor");
      workletNodeRef.current = workletNode;

      workletNode.port.onmessage = (workletEvent: MessageEvent<Float32Array<ArrayBuffer>>) => {
        if (ws.readyState !== WebSocket.OPEN) {
          return;
        }

        let samples = workletEvent.data;

        if (context.sampleRate !== TARGET_SAMPLE_RATE) {
          samples = resampleLinear(samples, context.sampleRate, TARGET_SAMPLE_RATE);
        }

        // nextPlaybackTimeRef is the AudioContext-clock time the queued reply
        // finishes, so this is exactly "the assistant is audible right now".
        const assistantPlaying =
          ECHO_GATE_ENABLED && context.currentTime < nextPlaybackTimeRef.current;
        const decision = nextEchoGate(echoGateRef.current, {
          level: rms(samples),
          assistantPlaying,
          now: performance.now(),
        });

        echoGateRef.current = decision.state;

        // Silence rather than dropping the frame: the server's turn detection
        // and STT both read this as a continuous stream, and skipping frames
        // would compress its sense of time.
        ws.send(
          floatToPCM16(decision.passThrough ? samples : new Float32Array(samples.length)),
        );
      };

      source.connect(workletNode);
    };

    ws.onmessage = handleServerMessage;

    ws.onclose = (closeEvent) => {
      const reason = interpretCloseCode(closeEvent.code);

      setCallStatus(reason.kind === "auth" ? "auth-error" : "ended");
      setStatusMessage(reason.message);
      teardown();
    };
  }, [activeWorkspace, assistantId, handleServerMessage, teardown]);

  const disconnect = useCallback(() => {
    wsRef.current?.close(1000, "caller disconnected");
  }, []);

  if (tenantStatus === "error") {
    return (
      <PageShell title="Test call">
        <ErrorText message={tenantError ?? "Could not load your workspace."} />
      </PageShell>
    );
  }

  if (tenantStatus === "loading" || (!loadError && !assistant)) {
    return (
      <PageShell title="Test call">
        <LoadingState />
      </PageShell>
    );
  }

  if (loadError) {
    return (
      <PageShell title="Test call">
        <ErrorText message={loadError} />
      </PageShell>
    );
  }

  if (!assistant) {
    return null;
  }

  const canStart = _RESTARTABLE.has(callStatus);
  const showDisconnect = callStatus === "connected";
  const busy = !canStart && !showDisconnect;

  return (
    <PageShell
      title={`Test call - ${assistant.name}`}
      description="Talk to your assistant right from the browser. No phone number needed."
    >
      {/*
        The assistant's own voice comes out of here, not straight out of the
        AudioContext - see playbackAudioRef. Rendered rather than constructed
        in JavaScript because a detached element silently plays nothing at
        all. It has no controls and so draws nothing; the caller drives the
        call from the buttons below.
      */}
      <audio ref={playbackAudioRef} playsInline aria-hidden="true" />

      <div className="mb-6">
        <Link
          href={`/assistants/${assistant.id}`}
          className="text-sm text-slate-400 hover:text-slate-300"
        >
          &larr; Back to assistant
        </Link>
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-sm text-slate-400">Status</p>
            <p className="text-lg font-medium">{STATUS_LABEL[callStatus]}</p>
            {/*
              Visible on purpose. A browser quietly serving a cached bundle
              looks exactly like a fix that did not work, and that ambiguity
              cost several rounds of debugging here - the voice worker was
              recording a client build two releases behind the one being
              tested. Now it can be read off the screen in one glance.
            */}
            <p className="mt-1 text-xs text-slate-500">build {CLIENT_BUILD}</p>
          </div>

          {showDisconnect ? (
            <Button variant="danger" onClick={disconnect}>
              Disconnect
            </Button>
          ) : (
            <Button onClick={startCall} disabled={busy}>
              Start test call
            </Button>
          )}
        </div>

        {statusMessage && callStatus !== "connected" && (
          <div className="mt-4">
            {callStatus === "ended" ? (
              <p className="text-sm text-slate-400">{statusMessage}</p>
            ) : (
              <ErrorText message={statusMessage} />
            )}
          </div>
        )}

        {callStatus === "connected" && (
          <div className="mt-4 flex items-center gap-2 text-sm text-slate-400">
            <span
              aria-hidden="true"
              className={`h-2.5 w-2.5 rounded-full ${speaking ? "bg-white" : "bg-green-500"}`}
            />
            {speaking ? "Norma is speaking" : "Listening"}
          </div>
        )}
      </Card>

      {inlineNotice && (
        <div className="mt-4">
          <ErrorText message={inlineNotice} />
        </div>
      )}

      <div className="mt-6">
        <Card>
          <h2 className="mb-4 text-lg font-semibold">Transcript</h2>

          {transcript.length === 0 ? (
            <EmptyState message="Nothing said yet. Start the call and say hello." />
          ) : (
            <ul className="space-y-2 text-sm">
              {transcript.map((line) => (
                <li key={line.id} className={line.speaker === "caller" ? "text-white" : "text-slate-400"}>
                  <span className="font-medium">
                    {line.speaker === "caller" ? "You: " : "Norma: "}
                  </span>
                  {line.text}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </PageShell>
  );
}
