# Feature: Session resilience

**From build-plan:** feature 20g

**Status:** complete

## Goal

Close CLAUDE.md's single hardest line in this feature line - "A crashed or timed-out session
forwards the call or takes a message. Silence is the worst possible failure" - for the three
providers the voice pipeline depends on (build-plan line: "provider timeouts, retries, and
failover to forwarding or message-taking instead of dead air"). Today, none of STT/LLM/TTS have
an explicit timeout: a hung provider call - not an error, just silence - blocks its stage forever,
and a caller who can no longer be transcribed or answered just sits on a connection that never
tells them anything is wrong. This is the last sub-item of build-plan item 20; completing it
finishes the real-time voice session engine as a whole.

## Design reference

None. Backend-only.

## Architecture decisions (read before building)

- **"Failover to forwarding or message-taking" cannot mean real telephony transfer or message
  delivery here - neither exists yet.** `ForwardingTarget` (item 29), `Call`/`CallLeg` (item 26),
  and SMS/email delivery (items 14/30/31) are all unbuilt; `/media/session` is a WebSocket test
  call, not a real phone leg. This feature's own scope is what the *media plane* can actually do
  today: detect that the session cannot continue, speak a bounded, best-effort apology instead of
  silence, and end its own participation cleanly (closing the pipeline). A real telephony
  integration, seeing that connection close (or a future explicit signal), is what actually
  forwards the call or takes a message - this feature produces the moment that decision hands
  off to, not the decision's real-world execution.
- **STT failure gets immediate failover, no retry.** `SpeechToTextProcessor._run_stream()`
  currently has no error handling at all around `self._provider.stream(...)` - an unhandled
  exception there just silently kills the background task, and the caller can never be
  transcribed again for the rest of the call, the single most severe failure this feature
  addresses. Retrying a live, continuous stream mid-session (reconnecting while replaying
  whatever audio arrived since the last successful chunk) is a materially bigger undertaking than
  retrying one bounded LLM/TTS call - out of scope here; STT failure goes straight to the
  session-failover path.
- **LLM and TTS calls are guarded by a *first-token*/*first-byte* timeout only, not a per-chunk
  one, and retried at most once from scratch.** A stream that has already produced output is, by
  definition, not hung - CLAUDE.md's own latency budget is itself stated against time-to-first-
  token/time-to-first-audio, the exact moment this guards. A mid-stream stall *after* a promising
  start is a rarer, harder-to-cleanly-recover-from case (you cannot "retry" a half-spoken
  sentence without confusing the caller) and is out of scope, documented as a known limitation -
  matching this feature line's established practice of naming a placeholder's real limitation
  rather than hiding it. A retry restarts the whole attempt (fresh retrieval, fresh LLM call, or
  a fresh synthesize() call for the same sentence) - safe because nothing has been spoken or
  pushed to the caller yet at a first-token/first-byte timeout.
- **A real bug found while running the full suite after the first working version of Step 3, not
  by inspection: the retry loop's own boundary was wrong.** The first draft caught
  `LLMProviderError` around the *entire* attempt - retrieval through the complete reply - and
  retried the whole thing on any failure, not just a first-token timeout. `MockLLM`'s own
  `failure` parameter yields its whole scripted response *then* raises, so a failure that lands
  after real content had already been streamed (a pre-existing, already-passing test) got
  retried too - meaning the caller would have heard the same partial reply spoken twice before
  finally getting `llm_error`. Fixed by narrowing the retry loop to cover only retrieval through
  the first-token wait; once past that point (whether a real first delta or a legitimately empty
  reply), a failure is never retried and immediately gives up exactly as the pipeline did before
  this feature existed. `LLMTurnProcessor._run_llm_turn` now reads as two clearly separated
  phases for exactly this reason - do not merge them back into one try block.
- **A shared `SessionResilienceTracker` counts *consecutive* fully-failed LLM turns, not TTS
  failures.** A single bad sentence out of several (the existing `tts_error` path) is a minor
  blemish - the other sentences still played. A `SpeechProviderError`/timeout on every sentence
  of every turn is much rarer given the documented shared-failure-domain between STT and TTS
  (one ElevenLabs outage takes out both) - STT's own immediate-failover path catches most of
  that case already. A separate TTS-specific consecutive-failure threshold is deferred, not
  built, to keep this feature's escalation logic to the one place it has an unambiguous
  per-turn success/failure signal (an LLM call either produces a full reply or it does not).
- **The failover apology and pipeline shutdown are handled entirely inside `TTSProcessor`, via a
  dedicated `session_failover` message, not routed through the existing `llm_delta`/`llm_complete`
  channel.** The existing channel carries real coupling to this feature line's own turn/generation
  tracking (item 20e's `_reply_in_progress`, item 20f's generation-guarded marks) that a synthetic,
  non-caller-originated "turn" would either have to fake correctly or silently corrupt. A
  dedicated message type bypasses the sentence queue/chunker/generation machinery entirely: cancel
  whatever reply is in flight, speak one fixed apology (bounded by the same first-byte timeout,
  best-effort - if TTS is also down, skip straight to closing), then push `EndFrame`. Both
  `SpeechToTextProcessor` (on a stream crash) and `LLMTurnProcessor` (once the consecutive-failure
  threshold is crossed) push this same message shape.
- **Ending the session is `self.push_frame(EndFrame())` from inside `TTSProcessor` itself - but
  this alone was verified, empirically, to not be enough.** A standalone script driving a real
  session confirmed `EndFrame` reaching the sink stops that frame's own audio-task loop, but
  `WorkerRunner.run()` in `main.py` never returned - the connection hung indefinitely, since
  `run()` only returns on an *external* stop signal, and nothing was providing one.
  `PipelineWorker` exposes exactly the missing piece: an `on_pipeline_finished` event, fired for
  every terminal frame (`StopFrame`/`EndFrame`/`CancelFrame`). `main.py` now registers a handler
  that calls `runner.end(reason=...)` specifically when the terminal frame is `EndFrame` (a normal
  caller-disconnect already ends via `CancelFrame`, which already has its own external trigger and
  needs no new handling). `runner.end()`, not `runner.cancel()`: `cancel()` does close the
  connection but does so by cancelling the runner's own task outright, which surfaced as a raw
  `concurrent.futures.CancelledError` out of `TestClient.__exit__` in the test suite (confirmed by
  trying `cancel()` first); `end()` is both the semantically correct call for a pipeline ending on
  its own terms and does not have that problem.
- **A `concurrent.futures.CancelledError` can still intermittently surface from `TestClient.
  __exit__` even with `runner.end()`, when the *server* (not the client) initiates the close -
  confirmed to be a test-harness-only artifact, not an application bug.** A standalone script
  against a real session independently confirmed the server-side close behavior itself is
  correct; a real Uvicorn deployment has no analogous "check a background future's result" step
  for this race to occur in - it is specific to how Starlette's `TestClient` bridges sync test
  code with the async ASGI app via a background thread. Both end-to-end tests that reach
  `session_failover` (and therefore a server-initiated close) wrap their `with` block in
  `try/except FutureCancelledError`, with the message lists they assert against populated before
  the `with` block so the exception - if it happens - never prevents those assertions from
  running against whatever was actually received.
- **`MockLLM`/`MockTTS` each gain a small `call_count` attribute, incremented once per
  `stream()`/`synthesize()` invocation** - mirrors `MockSTT.received_keywords`'s own precedent for
  a minimal, additive mock capability added because a specific test needs it (here: asserting a
  retry actually happened, by asserting the provider was called twice before giving up).

## In scope

- **`apps/voice/app/config.py`** - `LLM_FIRST_TOKEN_TIMEOUT_SECONDS` (default `8.0`),
  `TTS_FIRST_BYTE_TIMEOUT_SECONDS` (default `5.0`), `MAX_PROVIDER_RETRIES` (default `1`, i.e. one
  retry, two total attempts), `MAX_CONSECUTIVE_LLM_FAILURES` (default `2`).
- **`apps/voice/app/session_resilience.py`** (new) - pure logic: `SessionResilienceTracker`,
  `record_turn_failed() -> bool` (returns whether the consecutive-failure threshold is now
  crossed), `record_turn_succeeded() -> None` (resets the counter).
- **`apps/voice/app/media_session.py`**:
  - `SpeechToTextProcessor._run_stream()` wraps its `self._provider.stream(...)` consumption in a
    `try`/`except (SpeechProviderError, Exception)`, pushing `{"type": "session_failover",
    "reason": "stt_unavailable", "message": _FAILOVER_MESSAGE}` on any failure - no retry.
  - `LLMTurnProcessor._run_llm_turn` retries the whole attempt (retrieval + LLM stream) up to
    `MAX_PROVIDER_RETRIES` times on `(LLMProviderError, TimeoutError)`, guarding only the first
    delta with `asyncio.wait_for(..., timeout=LLM_FIRST_TOKEN_TIMEOUT_SECONDS)`. On final failure:
    pushes `llm_error` (existing, unchanged), calls the shared `SessionResilienceTracker`, and - if
    the threshold is now crossed - also pushes `session_failover` (`reason="llm_unavailable"`). On
    success: calls `record_turn_succeeded()`.
  - `TTSProcessor._speak` retries each sentence's synthesis up to `MAX_PROVIDER_RETRIES` times on
    `(SpeechProviderError, TimeoutError)`, guarding only the first chunk with
    `asyncio.wait_for(..., timeout=TTS_FIRST_BYTE_TIMEOUT_SECONDS)`. Final-failure behavior
    (pushing `tts_error`, continuing to the next queued sentence) is unchanged.
  - `TTSProcessor` gains a `_handle_session_failover(message)` reacting to the new message type:
    cancels any in-flight/queued reply, attempts one bounded, best-effort synthesis of the fixed
    apology text (no retry - the session is ending regardless), pushes whatever audio it managed,
    then pushes `EndFrame()`.
  - Pipeline builder constructs one shared `SessionResilienceTracker` per session and threads it
    into `LLMTurnProcessor`.
- **`packages/shared/norma_shared/mock_speech.py`** - `MockTTS` gains `call_count`; `MockSTT`
  gains `fail_without_draining` (not in the original plan - found while building Step 2: a
  scripted failure only ever raises after the audio iterator is fully drained, which never
  happens on a live connection whose caller is still talking, making the existing shape unusable
  for testing a mid-call STT crash specifically).
- **`apps/voice/app/mock_llm.py`** - `MockLLM` gains `call_count`.
- **End-to-end tests** (extending `test_media_session.py`): an STT crash triggers immediate
  failover (apology spoken, connection closes); an LLM that always fails triggers failover only
  once the consecutive-failure threshold is crossed (fewer failures than the threshold just get
  the existing `llm_error`, and the session keeps running); a provider that only ever times out
  (never raises) is retried the configured number of times before giving up, proven via
  `call_count`; a TTS timeout on one sentence still produces `tts_error` rather than hanging the
  whole reply.

## Out of scope

- **Real telephony call forwarding, message-taking, or SMS/email delivery.** Items 14, 29, 30, 31
  by name - see Architecture decisions for what this feature produces instead.
- **Retrying or reconnecting STT's own stream mid-session.** A materially bigger undertaking
  (buffered-audio replay to a reconnected provider); STT failure goes straight to failover.
- **A separate consecutive-failure threshold specifically for TTS.** Deferred - see Architecture
  decisions for why the existing per-sentence `tts_error` plus STT's own immediate-failover path
  is judged sufficient for now; item 49's observability is the eventual signal for a human to
  notice a persistent TTS-only outage.
- **Guarding against a mid-stream stall** (a stall *after* the first token/byte has already
  arrived). Only time-to-first-token/time-to-first-audio is guarded; documented as a known
  limitation, matching `is_semantically_complete`'s and `SentenceChunker`'s own precedent for
  naming a placeholder's real limitation.
- **Retrying the internal API calls** (`fetch_retrieved_context`, `fetch_llm_config`, etc.) -
  already resilient (existing timeout + fail-open); nothing to add here.
- **A stateful mock that fails N times then succeeds**, to test "the retry itself succeeds"
  specifically. `call_count` proves the retry loop runs the correct number of times and gives up
  correctly, which is what this feature's own correctness guarantee depends on; a richer
  call-count-driven pass/fail mock is a reasonable future enhancement, not required here.
- **Configurable or reason-specific apology text.** One fixed string for both failure reasons,
  matching `_LLM_ERROR_MESSAGE`/`_TTS_ERROR_MESSAGE`'s own fixed-string precedent.

## Build steps

- [x] **Step 1 - pure logic: `SessionResilienceTracker` and config constants**
  - `apps/voice/app/config.py`: the four new constants.
  - `apps/voice/app/session_resilience.py` (new): `SessionResilienceTracker`.
  *Done when:* `apps/voice/tests/test_session_resilience.py` proves: fewer consecutive failures
  than the threshold never reports "crossed"; reaching the threshold reports it exactly once;
  `record_turn_succeeded()` resets the counter, so a success between two failures prevents the
  threshold from ever being reached by summing them. Full `apps/voice` suite green. `ruff check
  apps/voice` clean.

- [x] **Step 2 - STT immediate failover**
  - `apps/voice/app/media_session.py`: `SpeechToTextProcessor._run_stream()`'s new
    `try`/`except`, pushing `session_failover`.
  - `packages/shared/norma_shared/mock_speech.py`: `MockSTT.fail_without_draining` (see
    Files/areas for why this was needed, not in the original plan).
  *Done when:* a new `test_media_session.py` test proves an `STT` provider that raises mid-stream
  (via `MockSTT`'s `failure` parameter, with `fail_without_draining=True`) produces exactly one
  `session_failover` message with `reason: "stt_unavailable"`, and no further transcript ever
  arrives afterward. Full `apps/voice` suite green. `ruff check apps/voice` clean.

- [x] **Step 3 - LLM timeout, retry, and threshold escalation**
  - `apps/voice/app/media_session.py`: `LLMTurnProcessor._run_llm_turn`'s retry/timeout wrapping;
    pipeline builder threads a `SessionResilienceTracker` into it.
  - `apps/voice/app/mock_llm.py`: `MockLLM.call_count`.
  *Done when:* a new test proves a `MockLLM` configured with `chunk_delay_seconds` well above
  `LLM_FIRST_TOKEN_TIMEOUT_SECONDS` (a stand-in for "never responds") is called exactly
  `MAX_PROVIDER_RETRIES + 1` times before `llm_error` is pushed (via `call_count`); a second test
  proves `MAX_CONSECUTIVE_LLM_FAILURES` separate failing turns are needed before `session_failover`
  appears - the turns before the last one produce only `llm_error`, matching today's unchanged
  behavior for an isolated blip. Full `apps/voice` suite green. `ruff check apps/voice` clean.

- [x] **Step 4 - TTS timeout and retry**
  - `apps/voice/app/media_session.py`: `TTSProcessor._speak`'s retry/timeout wrapping.
  - `packages/shared/norma_shared/mock_speech.py`: `MockTTS.call_count`.
  *Done when:* a new test proves a `MockTTS` configured with `time_to_first_byte_seconds` above
  `TTS_FIRST_BYTE_TIMEOUT_SECONDS` is called exactly `MAX_PROVIDER_RETRIES + 1` times before
  `tts_error` is pushed for that sentence (mirroring the LLM-side `call_count` test exactly). A
  separate "the next sentence still plays" proof was judged unnecessary: `_play_sentences`'s own
  per-sentence loop is untouched by this step and is already proven by the pre-existing
  exception-based `tts_error` test - a single MockTTS instance's `time_to_first_byte_seconds`
  applies uniformly to every call, so making only one sentence hang while another succeeds would
  need a mock capability this feature does not otherwise need. Full `apps/voice` suite green.
  `ruff check apps/voice` clean.

- [x] **Step 5 - the failover apology and pipeline shutdown**
  - `apps/voice/app/media_session.py`: `TTSProcessor._handle_session_failover`, wired into
    `process_frame`.
  - `apps/voice/app/main.py` (not in the original plan): registers an `on_pipeline_finished`
    handler that calls `runner.end()` on `EndFrame` - see Architecture decisions for why this was
    needed in addition to the `EndFrame` push itself.
  *Done when:* a new test drives an STT-triggered `session_failover` end to end and proves: the
  fixed apology's audio arrives, and the WebSocket connection actually closes server-side (not
  just "the test stopped reading") - verified by reading raw `ws.receive()` dicts directly until
  one reports `"websocket.close"`, rather than hanging forever. Full `apps/voice` suite green
  and stable across repeated runs (verified 16 consecutive full-suite runs with zero failures,
  after finding and fixing the `TestClient` exit-time race - see Architecture decisions). `ruff
  check apps/voice` clean. `docker compose build voice && docker compose up -d voice` succeeds;
  `/health` still 200.

## Files / areas

**New**
- `apps/voice/app/session_resilience.py`
- `apps/voice/tests/test_session_resilience.py`

**Modified**
- `apps/voice/app/config.py` (four new constants)
- `apps/voice/app/media_session.py` (STT failover, LLM retry/timeout/escalation, TTS retry/timeout,
  the new `session_failover` handling and pipeline shutdown, pipeline builder wiring)
- `apps/voice/app/main.py` (not in the original plan - the `on_pipeline_finished` handler needed
  to actually close the connection once `EndFrame` reaches the sink; see Architecture decisions)
- `apps/voice/app/mock_llm.py` (`call_count`)
- `packages/shared/norma_shared/mock_speech.py` (`MockTTS.call_count`, `MockSTT.
  fail_without_draining`)
- `apps/voice/tests/test_media_session.py` (new end-to-end tests)

**Unchanged**
- No frontend file. No `apps/api` file.

## Data / contracts

**`session_failover` message** - `{"type": "session_failover", "reason": "stt_unavailable" |
"llm_unavailable", "message": str}`. Pushed by `SpeechToTextProcessor` or `LLMTurnProcessor`;
`TTSProcessor` is the sole consumer, reacting by speaking `message` (best-effort, bounded) and
then ending the pipeline. `message` is always the same fixed, caller-safe string regardless of
`reason` (see Out of scope).

## Testing

Matches this feature line's established shape: pure logic (Step 1) gets full unit coverage;
each provider's resilience wiring (Steps 2-4) gets a focused end-to-end test through the real
pipeline (mock providers, no real network); the full failover-to-shutdown path (Step 5) gets its
own dedicated end-to-end test, including proving the connection actually closes - not merely that
messages stopped arriving.

## Notes for the AI

- **Wrap only the first delta/chunk in `asyncio.wait_for`, not the whole `async for` loop** - call
  `stream.__anext__()`/`gen.__anext__()` directly for the first item, then continue consuming the
  same generator normally with a plain `async for` for the rest. Catch `StopAsyncIteration` around
  the first-item call for an empty stream (a valid, non-error case - see `MockTTS.synthesize`'s own
  early return on zero total bytes).
- **`asyncio.wait_for`'s timeout on an async generator's `__anext__()` delivers `CancelledError`
  into the generator, exactly like item 20e's own barge-in cancellation finding** - this should
  already trigger correct cleanup in any real provider adapter; nothing new to fix there, just
  confirm it during testing rather than assuming it.
- **`(LLMProviderError, TimeoutError)` / `(SpeechProviderError, TimeoutError)` must be caught
  together** - `asyncio.TimeoutError` is `TimeoutError` since Python 3.11 (this project's runtime),
  and a provider's *own* timeout subclass (`LLMProviderTimeout`/`SpeechProviderTimeout`) is already
  a subclass of the base error, so a single `except (BaseError, TimeoutError)` catches every case
  that should trigger a retry.
- **Append the user's turn to `ConversationState` once, before the retry loop, never inside it** -
  retrying must not duplicate the caller's own message in conversation history across attempts.
- **`record_turn_succeeded()`/`record_turn_failed()` are called from `LLMTurnProcessor` only, never
  from `TTSProcessor`** - see Architecture decisions for why TTS failures don't feed this counter.
- **Verify the `EndFrame`-from-mid-pipeline shutdown mechanism empirically before relying on it in
  Step 5's own test** - if it does not close the WebSocket as expected, this is new-enough
  territory that a different mechanism may be needed; do not assume it works without direct
  verification, matching this feature line's own established practice.
- A push, if any, at the end of this feature does not need your explicit go-ahead - the user's
  `/feature` invocation for this item included the standing "don't ask for any permission, go
  with your recommendation" override, matching items 20a, 20b, 20d, 20e, and 20f. Completing this
  item also completes build-plan item 20 as a whole (all of 20a-20g checked) - check off both.
