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
  floatToPCM16,
  interpretCloseCode,
  pcm16ToFloat32,
  resampleLinear,
} from "@/lib/audio";

const VOICE_WS_URL = process.env.NEXT_PUBLIC_VOICE_WS_URL ?? "ws://localhost:8080";
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

    void audioContextRef.current?.close();
    audioContextRef.current = null;
    nextPlaybackTimeRef.current = 0;
  }, []);

  useEffect(() => () => teardown(), [teardown]);

  const flushPlayback = useCallback(() => {
    for (const node of playbackQueueRef.current) {
      try {
        node.stop();
      } catch {
        // Already stopped or finished naturally.
      }
    }
    playbackQueueRef.current = [];

    if (audioContextRef.current) {
      nextPlaybackTimeRef.current = audioContextRef.current.currentTime;
    }

    setSpeaking(false);
  }, []);

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
          flushPlayback();
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
        audio: { channelCount: 1 },
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
      `${VOICE_WS_URL}/media/session?ticket=${encodeURIComponent(ticket)}`,
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

        ws.send(floatToPCM16(samples));
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
