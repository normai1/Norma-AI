"use client";

import { useEffect, useRef, useState } from "react";

import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
} from "@/components/organizations/ui";
import { listVoices, type Voice } from "@/lib/voices";

export default function VoicesPage() {
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let cancelled = false;

    listVoices()
      .then((loaded) => {
        if (!cancelled) {
          setVoices(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load voices.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Stops any preview left playing if the operator navigates away mid-playback -
  // the Audio object isn't attached to the DOM, so nothing else would stop it.
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
    };
  }, []);

  function playPreview(voice: Voice) {
    if (!voice.preview_url) {
      return;
    }

    audioRef.current?.pause();

    const audio = new Audio(voice.preview_url);

    audioRef.current = audio;
    setPlayingId(voice.id);

    audio.addEventListener("ended", () => {
      setPlayingId((current) => (current === voice.id ? null : current));
    });

    audio.play();
  }

  if (error) {
    return (
      <PageShell title="Voices">
        <ErrorText message={error} />
      </PageShell>
    );
  }

  if (voices === null) {
    return (
      <PageShell title="Voices">
        <LoadingState />
      </PageShell>
    );
  }

  return (
    <PageShell
      title="Voices"
      description="Browse the voice catalogue and preview each one."
    >
      {voices.length === 0 ? (
        <EmptyState message="No voices are available yet." />
      ) : (
        <Card>
          <ul className="divide-y divide-slate-800">
            {voices.map((voice) => (
              <li
                key={voice.id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <span>
                  <span className="font-medium">{voice.name}</span>
                  <span className="ml-3 text-sm text-slate-500">
                    {voice.language}
                  </span>
                  {voice.gender && (
                    <span className="ml-3 text-sm text-slate-500">
                      {voice.gender}
                    </span>
                  )}
                </span>

                <Button
                  variant="secondary"
                  disabled={!voice.preview_url}
                  onClick={() => playPreview(voice)}
                >
                  {playingId === voice.id ? "Playing..." : "Play preview"}
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </PageShell>
  );
}
