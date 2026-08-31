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
  const transcriptIdRef = useRef(0);
  const partialCallerLineIdRef = useRef<number | null>(null);
  const partialAssistantLineIdRef = useRef<number | null>(null);

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

  const setCallerLine = useCallback((text: string, final: boolean) => {
    setTranscript((lines) => {
      const id = partialCallerLineIdRef.current;

      if (id !== null) {
        const updated = lines.map((line) => (line.id === id ? { ...line, text } : line));

        if (final) {
          partialCallerLineIdRef.current = null;
        }

        return updated;
      }

      const newId = transcriptIdRef.current++;

      if (!final) {
        partialCallerLineIdRef.current = newId;
      }

      return [...lines, { id: newId, speaker: "caller" as const, text }];
    });
  }, []);

  const appendAssistantDelta = useCallback((delta: string) => {
    setTranscript((lines) => {
      const id = partialAssistantLineIdRef.current;

      if (id !== null) {
        return lines.map((line) =>
          line.id === id ? { ...line, text: line.text + delta } : line,
        );
      }

      const newId = transcriptIdRef.current++;
      partialAssistantLineIdRef.current = newId;

      return [...lines, { id: newId, speaker: "assistant" as const, text: delta }];
    });
  }, []);

  const finalizeAssistantLine = useCallback((fullText: string) => {
    setTranscript((lines) => {
      const id = partialAssistantLineIdRef.current;
      partialAssistantLineIdRef.current = null;

      if (id !== null) {
        return lines.map((line) => (line.id === id ? { ...line, text: fullText } : line));
      }

      const newId = transcriptIdRef.current++;

      return [...lines, { id: newId, speaker: "assistant" as const, text: fullText }];
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
          setCallerLine(message.text, message.is_final);
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
    [playAudioChunk, setCallerLine, appendAssistantDelta, finalizeAssistantLine, flushPlayback],
  );

  const startCall = useCallback(async () => {
    if (!activeWorkspace) {
      return;
    }

    setInlineNotice(null);
    setTranscript([]);
    transcriptIdRef.current = 0;
    partialCallerLineIdRef.current = null;
    partialAssistantLineIdRef.current = null;

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
