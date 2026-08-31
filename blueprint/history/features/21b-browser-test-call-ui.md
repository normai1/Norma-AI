# Feature: Browser test-call UI

**From build-plan:** feature 21b

**Status:** complete

## Goal

Let an operator talk to their assistant from the browser with no phone number and no tunnel,
completing build-plan item 21 (21a already built and shipped the ticket-based authorization this
feature consumes). A new `/assistants/[assistantId]/test-call` page requests a short-lived
ticket, opens the `/media/session` WebSocket with it, streams microphone audio up as 16kHz mono
PCM16, plays synthesized speech back through the Web Audio API, reacts to the pipeline's
barge-in signal by flushing local playback immediately, and shows connection state, a live
transcript, and a speaking/listening indicator throughout - the default local development loop
CLAUDE.md section 22 and `AGENTS.md` both call out ("works without a tunnel... the default local
development loop").

## Design reference

None. No mockup exists for this page; CLAUDE.md section 25 describes the eventual split-view
editor (config left, live test call right) as a future direction, not something this feature
builds - the current editor (`app/(app)/assistants/[assistantId]/page.tsx`) is a single column
with no test-call surface at all, and folding this into a split view is a separate later
decision once the test-call experience itself exists and is proven. This feature ships it as its
own page, linked from the editor.

## Architecture decisions (read before building)

- **Capture and playback share one `AudioContext` created at `{sampleRate: 16000}`, not a
  hand-rolled resampler on the hot path.** `/media/session` speaks 16kHz mono PCM16 in both
  directions (`apps/voice/app/media_session.py`'s `AUDIO_SAMPLE_RATE_HZ`). Rather than capturing
  at the microphone's native rate (typically 48kHz) and resampling every frame in JavaScript,
  requesting a 16kHz `AudioContext` up front makes the browser's own audio graph resample the
  live mic input before it ever reaches the capture worklet - simpler, more robust, and exactly
  as low-latency as a native browser primitive can be. A pure-JS linear resampler
  (`lib/audio.ts`'s `resampleLinear`) is kept only as a fallback for the rare browser that
  ignores the requested context rate (checked at runtime via `audioContext.sampleRate`), not as
  the primary path.
- **Microphone capture uses an `AudioWorkletProcessor`, not the deprecated
  `ScriptProcessorNode`.** The worklet runs on the audio rendering thread and only posts
  fixed-size Float32 batches (320 samples = 20ms at 16kHz) to the main thread via
  `port.postMessage`; the main thread converts each batch to PCM16 (`lib/audio.ts`'s
  `floatToPCM16`) and sends it as a binary WebSocket frame. This keeps the audio thread itself
  free of network I/O, matching CLAUDE.md section 9's "no blocking I/O in the audio path"
  principle applied to the browser side of the pipeline.
- **Barge-in is enforced twice: once on the server (item 20e, already built) and once here.**
  `caller_speech_started` is pushed as an urgent, out-of-band message specifically so it can
  overtake already-queued playback messages; on the client, receiving it must immediately call
  `.stop()` on every currently-scheduled `AudioBufferSourceNode` and clear the local playback
  queue. Waiting for the server to simply stop sending more audio bytes is not sufficient -
  whatever was already sent and buffered client-side would keep playing, silently blowing past
  CLAUDE.md section 1's <200ms barge-in budget from the caller's perspective.
- **The close code from the server is interpreted, not just treated as "disconnected."**
  21a's `/media/session` route closes with `4401` for any invalid ticket and FastAPI itself
  closes with `1008` for a missing one; every other close is a normal end of call. A pure
  `interpretCloseCode(code)` function (`lib/audio.ts`) maps this so the UI can show "this test
  call couldn't be authorized" distinctly from "the call ended."
- **The ticket-issuing response shape and the WebSocket's `?ticket=...` contract are exactly
  21a's, unchanged.** `fetchTestCallTicket` in `lib/assistants.ts` calls
  `POST .../assistants/{id}/test-call-token` and gets back `{"ticket": str, "expires_in": int}`;
  the page opens `${NEXT_PUBLIC_VOICE_WS_URL}/media/session?ticket=<ticket>`.
- **A test call works on an unpublished (draft) assistant.** `resolve_llm_config` (consumed by
  `/media/session` today) already falls back to sensible defaults when `current_version_id` is
  `None`. The "Test call" link on the editor page is therefore never gated on `assistant.status`.

## In scope

- **`apps/web/lib/audio.ts`** (new) - pure, unit-tested helpers: `floatToPCM16`,
  `pcm16ToFloat32`, `resampleLinear`, `interpretCloseCode`.
- **`apps/web/public/worklets/pcm-capture-processor.js`** (new) - the `AudioWorkletProcessor`
  that batches raw mic Float32 samples and posts them to the main thread.
- **`apps/web/lib/assistants.ts`** - `fetchTestCallTicket`.
- **`apps/web/app/(app)/assistants/[assistantId]/test-call/page.tsx`** (new) - the full test-call
  page: ticket fetch, WebSocket lifecycle, mic capture, playback, barge-in, transcript/status
  display.
- **`apps/web/app/(app)/assistants/[assistantId]/page.tsx`** - a "Test call" link.
- **Tests**: `apps/web/lib/audio.test.ts` (14 Vitest cases). `apps/web/e2e/test-call.spec.ts` - an
  unauthenticated-redirect check plus a full authenticated golden-path test (register a user,
  create an org/workspace/assistant via the real API, click "Start test call" with Chromium's
  fake mic device, assert "Connected" is reached) - built beyond the spec's original
  "best-effort" hedge once the real dev stack proved reachable in this environment.

## Out of scope

- **Any change to `apps/voice` or `apps/api`.** 21a's contract is consumed exactly as built.
- **Folding this into a split-view assistant editor.** Revisit once this experience is proven.
- **Recording or saving a test call.** A test call is ephemeral by design.
- **Any UI for choosing STT/LLM/TTS providers or seeing per-turn latency numbers.** A real-call
  feature (needs `Call`/`TurnMetric` rows, item 26+).
- **Supporting a browser without `AudioWorklet` or without `getUserMedia`.** A clear inline error
  is shown instead of a silent failure; no polyfill.

## Build steps

- [x] **Step 1 - ticket fetch, WebSocket lifecycle, connection-state UI, editor link** - built as
  specced. `npm run test`/`build`/`lint` green.

- [x] **Step 2 - microphone capture** - built as specced, including `getUserMedia({ audio: {
  channelCount: 1 } })` and the self-contained worklet module. `npm run test`/`build`/`lint`
  green.

- [x] **Step 3 - playback and barge-in** - built as specced: gapless `AudioBufferSourceNode`
  queueing, `.stop()` on every tracked node on `caller_speech_started`, speaking/listening
  indicator. `npm run test`/`build`/`lint` green.

- [x] **Step 4 - transcript/status display, error and failover handling, E2E smoke test** - built
  as specced, plus the E2E test was extended past its original "best-effort, may not be runnable
  here" hedge into a full authenticated golden-path test once the real stack was confirmed
  reachable (see Testing below). `npm run test`/`build`/`lint`/`npx playwright test` all green.

## Files / areas

**New**
- `apps/web/lib/audio.ts`, `lib/audio.test.ts`
- `apps/web/public/worklets/pcm-capture-processor.js`
- `apps/web/app/(app)/assistants/[assistantId]/test-call/page.tsx`
- `apps/web/e2e/test-call.spec.ts`

**Modified**
- `apps/web/lib/assistants.ts` (`fetchTestCallTicket`)
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx` ("Test call" link)

**Unchanged**
- No `apps/api` or `apps/voice` file - 21a's contract is consumed as-is.
- `apps/web/lib/auth.ts` - `authorizedJson` reused unchanged.
- `NEXT_PUBLIC_VOICE_WS_URL` - already defined in `.env.example` and `docker-compose.yml`.

## Data / contracts

Consumed as locked by 21a: `POST .../assistants/{assistant_id}/test-call-token` ->
`{"ticket": str, "expires_in": int}`; `wss://<voice-host>/media/session?ticket=<jwt>` with the
full message-type contract (`transcript`, `turn_ended`, `caller_speech_started`, `llm_delta`,
`llm_complete`, `llm_error`, `tts_error`, `reply_finished`, `session_failover`); close codes
`4401`/`1008` both mean "not authorized."

New in this feature, internal only: `lib/audio.ts`'s four pure functions.

## Testing

Pure logic (`lib/audio.ts`) has full Vitest unit coverage (14 cases) - PCM conversion, clamping,
resampling, and close-code interpretation. `lib/assistants.ts`'s thin `fetchTestCallTicket`
wrapper stays untested, matching that file's existing precedent.

The full golden path (register -> create org/workspace/assistant via the real API -> inject
tokens -> click "Start test call" with Chromium's fake-media-device flags -> assert "Connected")
is proven end to end by `apps/web/e2e/test-call.spec.ts` against the real running dev stack
(apps/api, apps/voice, apps/web all in Docker). This exercises 21a's ticket issuance and
`/media/session` verification together with 21b's frontend for the first time as a whole system.
A second test confirms the route is behind the app's existing auth guard.

Local manual verification uses mock providers (the existing defaults) - real audio bytes flow in
both directions and the full transport/playback/barge-in mechanism is provable, but a meaningful
spoken reply requires real provider keys, which is a local-environment constraint, not a feature
gap.

## Notes for the AI

- `AudioContext` is created at `{ sampleRate: 16000 }`; `resampleLinear` is the fallback path,
  not the primary one.
- The worklet file (`public/worklets/pcm-capture-processor.js`) is self-contained - no imports
  from `lib/audio.ts`, since `AudioWorkletProcessor` code runs in its own global scope.
- Every scheduled `AudioBufferSourceNode` is tracked in an array so barge-in can stop all of them,
  not just the most recent one.
- **A TypeScript 5.7+/DOM-lib quirk**: `Float32Array` unparameterized defaults to
  `Float32Array<ArrayBufferLike>`, but `AudioBuffer.copyToChannel` requires
  `Float32Array<ArrayBuffer>` specifically. `lib/audio.ts`'s three functions are explicitly typed
  `Float32Array<ArrayBuffer>` (not bare `Float32Array`) to satisfy this - found via a real
  `npm run build` type-check failure, not anticipated in the spec.
- **A second real build error** (`TS2367`, a genuinely redundant `callStatus ===
  "mic-unsupported"` check inside a `disabled={busy || ...}` expression, caught by TypeScript's
  aliased-condition narrowing since `busy` already accounted for that state) was fixed by
  removing the redundant clause and renaming `_IDLE_LIKE` to `_RESTARTABLE` with
  `"mic-unsupported"` excluded from the set directly, rather than double-checking it separately.
- **Both `norma-api` and `norma-voice` docker containers were found crash-looping** on
  `ModuleNotFoundError: No module named 'norma_shared.voice_session_ticket'` during this
  feature's verification - stale images from before 21a's dependency was rebuilt in. Both were
  rebuilt and force-recreated (`docker compose build <service> && docker compose up -d
  --force-recreate --no-deps <service>`) before the golden-path E2E test could run. A `docker
  compose up -d <service>` alone did **not** recreate an already-running container from a newer
  image in this environment; `--force-recreate` was required.
- **The already-running `norma-web` container (25 hours old) also caused a false-negative**:
  Playwright's `webServer` with `reuseExistingServer: true` silently reused it instead of
  starting a fresh dev server, so the first e2e run against the new route 404'd even though the
  code was correct - exactly the `norma-web`-restart pitfall AGENTS.md's Commands section already
  documents for new route directories on a Docker bind mount on Windows. Restarting the container
  fixed it.

## Findings

None recorded against this feature; the ledger's outstanding entries all predate it and are
unrelated.
