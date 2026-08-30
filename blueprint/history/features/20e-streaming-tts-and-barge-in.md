# Feature: Streaming TTS and barge-in

**From build-plan:** feature 20e

**Status:** complete

## Goal

Turn the LLM's streamed text reply (20d) into spoken audio, and make the assistant stop talking
the instant the caller starts: "sentence-chunked playback starting before the LLM finishes, with
immediate cancellation on caller speech" (build-plan line), matching CLAUDE.md's two non-
negotiable rules - "start speaking before the LLM finishes" and "barge-in cancels playback... and
discards the abandoned response." This is the first feature where the media plane actually plays
audio back to the caller, not just JSON text messages.

## Design reference

None. Backend-only.

## Architecture decisions (read before building)

- **Sentence chunking is pure logic, a new `SentenceChunker`**, mirroring `turn_detection.py`'s
  and `conversation.py`'s own pure-module precedent. It is a placeholder heuristic (splits on
  terminal punctuation) with the same honestly-documented limitation `is_semantically_complete`
  already carries - it will mis-split on abbreviations ("Dr. Smith") - real sentence-boundary
  detection is out of scope for this spike.
- **A real architectural gap found while designing this feature: `TurnDetector.feed_audio`'s
  early return (`if self._turn_ended: return`) silently stops all VAD analysis the moment a turn
  ends.** That was correct for items 20c/20d, where nothing needed to observe audio during the
  "reply in flight" window. Barge-in is exactly that observation: it must detect the caller
  speaking *during* that window. The early return is removed; VAD analysis and a new
  `is_speaking` property now always update, while the turn-ending-specific bookkeeping
  (`_ever_spoken`/`_silence_since`/`_recompute`) still only runs when `_turn_ended` is `False`,
  preserving items 20c/20d's behavior exactly. Verified this doesn't change any existing
  `test_turn_detection.py` assertion (none of them fed audio while already latched).
- **A second real gap, found the same way: `reset_for_next_turn()`'s ownership belongs to
  whichever stage finishes last, and that is no longer the LLM call.** Item 20d called it from
  `LLMTurnProcessor`'s `finally` block, correct when the LLM's own text stream was the entire
  reply. Now the reply is not actually over when the LLM finishes generating - it is over once
  the caller has heard all of it (or been cancelled by barge-in). Reset ownership moves to
  `TTSProcessor`, which calls it once the last sentence of a reply has finished playing (tracked
  via "LLM signaled done" + "sentence queue empty" + "nothing currently playing", each checked at
  the two points where any of those three can newly become true) or immediately on barge-in.
  `LLMTurnProcessor` no longer calls `reset_for_next_turn()` itself.
- **Barge-in fires on VAD's confirmed `SPEAKING` state, not the earlier `STARTING` state.** The
  earlier signal would react faster (helping the <200ms budget) but risks false positives from
  a noise burst that never sustains into real speech, wrongly cutting off the assistant. This
  spike takes the more conservative choice and documents the trade-off; real tuning against
  actual latency numbers (and whether `STARTING` should drive barge-in specifically, using a
  separate, faster-reacting `VADParams` than the one governing turn-ending `stop_secs`) is
  deferred to item 61's real load/latency validation, the same deferral 20c already made for its
  own sensitivity constants.
- **The barge-in signal, `{"type": "caller_speech_started"}`, is emitted by `TurnDetectionProcessor`
  on every genuine speech-onset edge, not just during an active reply.** It fires the same way at
  the start of an ordinary turn (where `LLMTurnProcessor`/`TTSProcessor` have nothing in flight to
  cancel, so it is a harmless no-op) and at a genuine interruption (where it cancels whatever is
  running). No separate "is anything currently speaking" check is needed upstream - both
  downstream processors already no-op correctly when there is nothing to cancel.
- **Barge-in also cancels the in-flight LLM call, not just TTS playback.** `LLMTurnProcessor` also
  observes `caller_speech_started` and cancels its own task if one is running - otherwise a
  finishing LLM call would keep feeding text for an abandoned reply into a freshly-reset
  `SentenceChunker`, and the assistant would start speaking again with no matching caller turn
  behind it. This also stops wasting LLM cost on a reply nobody will hear (CLAUDE.md section 21's
  cost-consciousness).
- **Barge-in cancellation is real `asyncio.Task.cancel()`, not `TextToSpeechProvider`'s documented
  `.aclose()`-based contract - verified empirically, and `MockTTS` needed a small fix to match.**
  Tested directly: cancelling the task consuming an async generator delivers `CancelledError` to
  the generator's suspended frame, never `GeneratorExit`; calling `.aclose()` afterward is a
  no-op (the generator already terminated). `MockTTS.synthesize()`'s cancellation detection only
  caught `GeneratorExit` (correct for its one existing test, which closes the iterator explicitly
  and sequentially - never via a concurrently-cancelled task), so a `CancelledError`-based
  barge-in would never have set `.cancelled = True`, breaking the exact test the class's own
  docstring says it exists for. Fixed in `packages/shared/norma_shared/mock_speech.py` to catch
  both exception types identically; the existing explicit-`.aclose()` test is unaffected. A second
  refinement was needed once the actual test was written: the original `try` only wrapped the
  chunk-yielding loop, not the earlier `time_to_first_byte_seconds` sleep - so a cancellation
  arriving before any audio is yielded at all (barge-in's single most important case) still went
  uncaught. Widened the `try` to cover that sleep too. The real `ElevenLabsTTS.synthesize()`
  cleans up correctly either way - its `async with client.stream(...)` closes the HTTP connection
  during normal exception-propagation cleanup regardless of which exception unwinds it - so this
  fix is purely about keeping the mock an honest stand-in, not a production behavior change.
- **A TTS provider failure emits one `{"type": "tts_error", ...}` message per sentence, not a
  retry** - the same "provider failure never produces silence, full retry/failover is item 20g's
  job" reasoning item 20d already established for `llm_error`. Unlike an LLM failure, a TTS
  failure cannot itself be spoken (the thing that's broken is exactly the thing that would speak
  it), so the JSON message is this spike's only observable signal; a real spoken/dial-out fallback
  is squarely item 20g.
- **On `llm_error`, any buffered-but-not-yet-complete sentence fragment is discarded, not spoken -
  found while writing this feature's own failure test.** `llm_complete`'s trailing fragment is the
  tail of a genuinely intended reply and gets flushed to speech as usual; `llm_error`'s fragment is
  an abandoned, mid-thought scrap (e.g. the single word "Sure" before a failure) - speaking a
  random cut-off word out of context would be a worse caller experience than staying silent for
  that turn, so `TTSProcessor` resets the chunker instead of flushing it on that path.
- **A significant, empirically-verified finding while wiring barge-in: `TTSProcessor` cannot use
  `turn_detector.turn_ended()`'s live value to decide whether a `caller_speech_started` is a real
  interruption.** Pipecat gives every `FrameProcessor` its own per-processor frame queue, so a
  fast upstream processor (`TurnDetectionProcessor`) can race ahead - mutating the *shared*
  `TurnDetector` object's state for a *later* frame - before an *earlier* message it already
  pushed (this session's very first `caller_speech_started`) has even been delivered to a slower
  downstream consumer's queue. Peeking at the shared object's live state at message-processing
  time therefore answers a question about a different, later moment than the message actually
  represents - confirmed by tracing an actual failing test, not by inspection alone. Fixed by
  giving `TTSProcessor` its own local `_reply_in_progress` flag, set `True` on `turn_ended` (the
  moment a reply logically begins, even before the LLM's first token) and `False` on any reset -
  mirroring `LLMTurnProcessor`'s own `self._llm_task`, which was already correctly local rather
  than shared. `turn_ended` and `caller_speech_started` are both pushed by the same upstream
  processor and travel the same downstream chain, so their *relative arrival order* at
  `TTSProcessor` is reliable even though the shared detector's *live state* is not.
- **`voice_id`/`speech_rate` get their own new internal endpoint, `GET .../tts-config`, not folded
  into `llm-config`.** Same reasoning item 20d already used to keep `llm-config` and `retrieve`
  separate: different concern, fetched once at session setup, no benefit to merging.
  `speech_rate` maps directly to `TextToSpeechProvider.synthesize`'s `speed` parameter - no
  rescaling, since both already use the same `0.5`-`2.0`-ish notion of a rate multiplier
  (confirmed by the schema's own `ge=0.5, le=2.0` bounds matching typical TTS speed-multiplier
  ranges).
- **The unpublished-assistant fallback for `voice_id` is a fixed placeholder, not a real catalogue
  voice.** Unlike `system_prompt`/`creativity`/`turn_sensitivity`, `voice_id` has no schema
  default - it is a required field an assistant cannot be created without. This fallback is
  therefore unreachable by any assistant that could plausibly receive a real call; it exists only
  so a stray test-call attempt against a genuinely unpublished assistant fails safely instead of
  500ing.
- **`TurnDetector`'s reset needed a deeper redesign than Step 5 first assumed, found via a
  hanging Step 6 end-to-end barge-in test, not by inspection.** The original design cleared
  `ever_spoken`/`silence_since`/the final-transcript buffer unconditionally inside
  `reset_for_next_turn()`. Because that reset is now delivered asynchronously (a
  `caller_speech_started` message reacted to by `TTSProcessor`, not synchronously alongside
  whatever caused it), the caller's *entire next turn* - speak, go quiet, get transcribed - can
  finish arriving before the reset actually runs. An unconditional clear silently threw all of
  that away. The fix, arrived at over several rounds of direct source instrumentation (printing
  from inside `turn_detection.py` itself, after a subclass-based tracing attempt silently
  produced nothing): `feed_audio` now updates `ever_spoken`/`silence_since` unconditionally
  (not gated on `turn_ended()`); `_recompute()` alone clears them, synchronously, at the exact
  moment it sets `turn_ended` `True`; the buffer accumulating toward the *next* turn
  (`_pending_transcript`) is a separate field from the stable, never-cleared snapshot
  `last_final_transcript` exposes (`_ended_turn_text`) - conflating the two caused the same
  silent-discard bug for final-transcript text specifically; and `reset_for_next_turn()` itself
  now calls `_recompute()` as its last step, since otherwise nothing re-examines an
  already-fully-arrived next turn once the reset finally runs (no *new* `feed_audio`/
  `feed_transcript` call is left to trigger it).
- **Even with `TurnDetector`'s state now correct, the second turn's `turn_ended` message still
  never reached the caller - a second, structural gap in `TurnDetectionProcessor`, also found via
  the same hanging test.** Pipecat's pipeline is strictly unidirectional: `TTSProcessor`
  (downstream) can make the shared detector's `turn_ended()` become `True` again by calling
  `reset_for_next_turn()`, but nothing can make a *further* frame reach `TurnDetectionProcessor`
  afterward to trigger its own `_maybe_emit_turn_ended()` the normal way - every frame belonging
  to that second turn already flowed through it earlier, while still latched. Fixed with a
  narrow, deliberate exception to this feature's usual frame-only cross-processor communication:
  `TurnDetectionProcessor` gains a public `recheck()` method (re-running its two emission checks
  without a new frame arriving), and `TTSProcessor` holds a direct reference to the
  `TurnDetectionProcessor` instance (not just the shared `TurnDetector`), calling `recheck()`
  immediately after every `reset_for_next_turn()`. `recheck()` also has to force its own
  `_previously_ended` edge-tracking flag back to `False` first - otherwise the edge-triggering
  still misses the second turn, since `_previously_ended` was already `True` from the first turn
  and the detector's own brief `False` state lives and dies entirely inside
  `reset_for_next_turn()`'s synchronous body, never observed by this processor.
- **A third, independent gap: Pipecat's own output transport silently drops the last fraction of
  every reply's audio unless a `TTSStoppedFrame` is pushed.** `handle_audio_frame` (Pipecat's
  `base_output.py`) only auto-flushes *complete* `audio_chunk_size` chunks (1280 bytes by
  default); any smaller trailing remainder sits buffered indefinitely, discarded only if a later
  `_bot_stopped_speaking()` clears it, or flushed only if a `TTSStoppedFrame` arrives first. This
  feature never pushed one, so the true tail of every sentence - real spoken content, not
  silence - was silently lost. Found the same way as the two gaps above: instrumenting the real
  code (this time `RawAudioFrameSerializer.serialize()`, to see exactly what bytes actually
  reached the wire) after a still-hanging end-to-end test kept coming up short by exactly one
  partial chunk. Fixed by having `TTSProcessor` push a `TTSStoppedFrame` right after every
  sentence's own playback ends, success or cancellation alike. This is a deliberate, documented
  tradeoff, not a full fix: pushing it unconditionally also flushes (rather than discards) an
  abandoned sentence's own already-buffered tail on barge-in - a few tens of milliseconds of
  stale audio - which this spike accepts as preferable to that same audio silently bleeding into
  the *next* reply instead. Fully avoiding it would mean wiring Pipecat's own
  `InterruptionFrame`/bot-speaking machinery, well beyond this feature's scope; a Step 6 test
  assertion was loosened from an exact byte-count match to "at least this many bytes arrived" to
  match, since the transport's own silence-padding on the flushed remainder is an implementation
  detail this feature has no business pinning an exact number to.

## In scope

- **`apps/voice/app/sentence_chunker.py`** - pure logic: `SentenceChunker.feed(delta: str) ->
  list[str]` (returns zero or more newly-complete sentences), `flush() -> str | None` (the
  trailing, possibly-incomplete fragment, if any), `reset()` (discards buffered text - used on
  barge-in).
- **`apps/voice/app/turn_detection.py`** - remove `feed_audio`'s early return (see Architecture
  decisions); add `is_speaking: bool` property, always current regardless of `turn_ended()`'s
  latch state.
- **`packages/shared/norma_shared/mock_speech.py`** - `MockTTS.synthesize` also catches
  `asyncio.CancelledError` alongside `GeneratorExit` for its `.cancelled` bookkeeping (see
  Architecture decisions).
- **`apps/voice/app/provider_factory.py`** - `get_tts_provider(name=None) -> TextToSpeechProvider`,
  mirroring `get_stt_provider`'s exact shape (reusing the same `UnknownSpeechProviderError`/
  `MissingElevenLabsApiKeyError`, since both are ElevenLabs-backed).
- **`apps/voice/app/config.py`** - `TTS_PROVIDER` (default `"mock"`).
- **`apps/api/app/services/tts_config.py`** - `TTSConfig` (frozen dataclass: `voice_id: str`,
  `speech_rate: float`); `resolve_tts_config(db, assistant_id) -> TTSConfig`: looks up the
  assistant (raising `AssistantNotFound` if missing), returns the fixed defaults
  (`voice_id="default"`, `speech_rate=1.0`, matching the schema's own default) if unpublished,
  otherwise reads both fields straight off the current version.
- **`apps/api/app/api/internal/tts_config.py`** - `GET
  /internal/v1/assistants/{assistant_id}/tts-config` -> `{"voice_id": str, "speech_rate": float}`.
  Same `RequireInternalSecret` auth as the existing internal endpoints.
- **`apps/voice/app/tts_config_client.py`** - `TTSConfig` (duplicated, not shared - same judgment
  call item 20d already made for `LLMConfig`); `fetch_tts_config(assistant_id) -> TTSConfig`,
  fails open to the same fixed defaults on any error.
- **`apps/voice/app/media_session.py`**:
  - `TurnDetectionProcessor` also emits `{"type": "caller_speech_started"}` on the edge into
    `is_speaking` (see Architecture decisions).
  - `LLMTurnProcessor` also cancels its in-flight task on `caller_speech_started`; no longer
    calls `reset_for_next_turn()` (ownership moves to `TTSProcessor`).
  - `TTSProcessor` (new `FrameProcessor`): observes `turn_ended`/`llm_delta`/`llm_complete`/
    `llm_error` and feeds text through a `SentenceChunker`, synthesizing and playing each complete
    sentence via a single sequential player task (never overlapping playback) as soon as it is
    ready - not waiting for `llm_complete`. Pushes synthesized audio as `OutputAudioRawFrame`. On
    `caller_speech_started`, cancels the currently-playing sentence's task, discards any
    still-queued sentences from that reply, resets the chunker, and resets the turn detector
    immediately - but only if a reply is actually in progress, tracked via this processor's own
    local `_reply_in_progress` flag (see Architecture decisions for why `turn_detector.turn_ended()`
    itself is not a reliable check here). On a `SpeechProviderError` from the TTS provider, pushes
    `{"type": "tts_error", "text": ...}` for that sentence and continues to the next queued one (a
    single bad sentence doesn't abandon the rest of the reply); on `llm_error`, any buffered
    fragment is discarded rather than spoken (see Architecture decisions). Resets the turn
    detector once the reply's last sentence has genuinely finished playing (see Architecture
    decisions for the exact three-condition check), pushing `{"type": "reply_finished"}` every
    time it resets (normal completion or barge-in) - the only observable signal that the reset,
    which now happens in this processor's own background task, has actually occurred. Cancels its
    own in-flight playback task on `EndFrame`/`CancelFrame`.
  - Pipeline builder takes the additional TTS provider/config dependencies and wires
    `TTSProcessor` in after `LLMTurnProcessor`.
- **`apps/voice/app/main.py`** - fetches TTS config once at session setup (alongside the existing
  fetches), constructs the TTS provider, wires it all into the pipeline builder.
- **End-to-end tests** (extending `test_media_session.py`, and updating its three existing
  turn/LLM tests for the new pipeline stage): a happy-path reply proving audio for the first
  sentence arrives before the LLM has finished streaming the rest; a TTS-failure case (`tts_error`
  message, no crash, the rest of the reply is unaffected... unless it was the only sentence);
  a barge-in case (caller interrupts before any audio for the pending reply has been sent; the
  interrupting speech is then itself correctly detected and answered as a new turn); all through
  the real WebSocket pipeline with `MockSTT`/`MockLLM`/`MockTTS`/a scripted fake VAD analyzer - no
  real network, no real model, no real voice.

## Out of scope

- **A real semantic sentence-boundary model.** Explicitly named above; `SentenceChunker` is a
  documented placeholder, same status as `is_semantically_complete`.
- **Tuning barge-in's VAD state (`STARTING` vs `SPEAKING`) or its start/stop timing against a real
  <200ms measurement.** Item 61's real load/latency validation, matching item 20c's own deferral
  for its analogous constants.
- **Per-turn latency instrumentation / `TurnMetric` rows**, including measuring this feature's own
  barge-in latency. Item 20f.
- **Retry, timeout-driven failover, or a spoken/dial-out fallback on TTS failure.** Item 20g by
  name; this feature only guarantees one JSON signal per failed sentence instead of silence.
- **Ambient sound / background audio mixing.** `AssistantVersion.ambient_sound` exists but nothing
  reads it yet; out of scope here.
- **A telephony-side audio format/codec concern** (8kHz mu-law, etc.) - everything here stays in
  the canonical 16kHz PCM format; transcoding at the telephony edge is item 23+.
- **Persisting recordings or transcript audio.** Item 20's recording/retention rules (CLAUDE.md
  section 20) apply once calls are real; nothing is written to storage here.

## Build steps

- [x] **Step 1 - pure logic: sentence chunking and continuous VAD tracking**
  - `apps/voice/app/sentence_chunker.py` (new): `SentenceChunker`.
  - `apps/voice/app/turn_detection.py`: remove `feed_audio`'s early return; add `is_speaking`.
  *Done when:* `apps/voice/tests/test_sentence_chunker.py` proves: `feed()` returns a sentence
  exactly when terminal punctuation completes it (and none before then); multiple complete
  sentences in one `feed()` call all come back; `flush()` returns the trailing fragment (or
  `None` when nothing is buffered); `reset()` discards buffered text. `apps/voice/tests/
  test_turn_detection.py` gains coverage proving `is_speaking` reflects the latest VAD state
  correctly even while `turn_ended()` is already latched `True` (the exact case that was
  previously silently skipped), and that the full existing suite still passes unchanged (proving
  the early-return removal preserves items 20c/20d's behavior). Full `apps/voice` suite green.
  `ruff check apps/voice` clean.

- [x] **Step 2 - `MockTTS` cancellation fix and `apps/voice` TTS provider wiring**
  - `packages/shared/norma_shared/mock_speech.py`: `MockTTS.synthesize` also catches
    `asyncio.CancelledError`.
  - `apps/voice/app/provider_factory.py`: `get_tts_provider`.
  - `apps/voice/app/config.py`: `TTS_PROVIDER`.
  *Done when:* a new test in `apps/api/tests/test_speech_providers.py` proves cancelling the task
  consuming `MockTTS.synthesize()` (not just calling `.aclose()` directly, which the existing test
  already covers) also sets `.cancelled = True`; the existing explicit-`.aclose()` test still
  passes unchanged. `apps/voice/tests/test_provider_factory.py` (already exists, covering
  `get_stt_provider` - extended, not replaced) gains the same coverage shape for
  `get_tts_provider`: resolves `"mock"`/`"elevenlabs"`, raises for an unknown name and a missing
  API key. Full `apps/api` and `apps/voice` suites green. `ruff check` clean on both.

- [x] **Step 3 - internal tts-config endpoint + `apps/voice` client**
  - `apps/api/app/services/tts_config.py`, `app/api/internal/tts_config.py`, registered in
    `main.py`.
  - `apps/voice/app/tts_config_client.py`.
  *Done when:* `apps/api` gets `tests/test_tts_config.py` (service-level: returns the real
  `voice_id`/`speech_rate` for a published assistant; falls back to the fixed defaults for an
  unpublished one; raises `AssistantNotFound` for an unknown assistant) and
  `tests/test_internal_tts_config.py` (route-level: success, 404, 401). `apps/voice/tests/
  test_tts_config_client.py` proves success, non-200, and connection-failure all resolve
  correctly (fake `httpx` transport, no real network). Full `apps/api` and `apps/voice` suites
  green. `ruff check` clean on both.

- [x] **Step 4 - `caller_speech_started` signal and `LLMTurnProcessor` barge-in reaction**
  - `apps/voice/app/media_session.py`: `TurnDetectionProcessor` emits `caller_speech_started`;
    `LLMTurnProcessor` cancels its task on it. **`LLMTurnProcessor` keeps calling
    `reset_for_next_turn()` in its own `finally` block for this step** - moving that ownership to
    `TTSProcessor` only happens in Step 5, where the replacement mechanism actually starts to
    exist; removing it here first would leave nothing resetting the detector between the two
    steps and break every multi-turn test in between, violating "each step leaves the app
    working."
  *Done when:* an extended `apps/voice/tests/test_media_session.py` test proves
  `caller_speech_started` arrives on a genuine speech-onset edge (not on every audio frame while
  already speaking) and that an in-flight LLM call is cancelled (no `llm_complete`/`llm_error`
  arrives for it) when the caller speaks before the LLM finishes - and that the detector still
  correctly resets afterward (a following turn is still detectable), proving this step alone
  doesn't regress 20d's multi-turn behavior. The three existing turn/LLM tests are updated only
  as needed for this step's changes (not yet for TTS, which Step 6 adds). Full `apps/voice` suite
  green. `ruff check apps/voice` clean.

- [x] **Step 5 - `TTSProcessor`**
  - `apps/voice/app/media_session.py`: `TTSProcessor`; pipeline builder takes the TTS
    provider/config and wires the new stage in. `LLMTurnProcessor`'s `finally` block stops
    calling `reset_for_next_turn()` - `TTSProcessor` now owns it (see Architecture decisions).
  - `apps/voice/app/main.py`: fetch TTS config, construct the TTS provider, wire it through.
  *Done when:* the three existing `test_media_session.py` turn/LLM tests are updated for the new
  stage (mocking `fetch_tts_config`/`get_tts_provider` as needed, matching how they were already
  updated in items 20c/20d for each newly-added stage) and still pass. Full `apps/voice` suite
  green. `ruff check apps/voice` clean.

- [x] **Step 6 - end-to-end TTS and barge-in tests**
  *Done when:* three new WebSocket tests pass: (1) a two-sentence LLM reply where the first
  sentence's audio bytes are observed to arrive before the LLM's final delta/`llm_complete` (using
  `MockLLM`'s `chunk_delay_seconds` to create the window, and generic `ws.receive()` message
  collection to avoid assuming exact text/binary interleaving); (2) a TTS failure produces
  `tts_error` for that sentence with no crash; (3) barge-in - a pending reply's audio never
  starts (using `MockTTS`'s `time_to_first_byte_seconds` to guarantee zero bytes are emitted
  before the interruption arrives), and the interrupting speech is itself detected and answered
  as a new turn. All three use a scripted fake VAD analyzer and mock providers throughout - no
  real Silero model, no real Anthropic/ElevenLabs call. Full `apps/voice` suite green. `ruff check
  apps/voice` clean. `docker compose build voice && docker compose up -d voice` succeeds;
  `/health` still 200.

## Files / areas

**New**
- `apps/voice/app/sentence_chunker.py`, `app/tts_config_client.py`
- `apps/voice/tests/test_sentence_chunker.py`, `test_tts_config_client.py`
- `apps/api/app/services/tts_config.py`, `app/api/internal/tts_config.py`
- `apps/api/tests/test_tts_config.py`, `test_internal_tts_config.py`

**Modified**
- `apps/voice/app/turn_detection.py` (`feed_audio` early-return removal, `is_speaking`)
- `packages/shared/norma_shared/mock_speech.py` (`MockTTS` cancellation fix)
- `apps/voice/app/provider_factory.py` (`get_tts_provider`)
- `apps/voice/app/config.py` (`TTS_PROVIDER`)
- `apps/voice/app/media_session.py` (`TurnDetectionProcessor`'s new emission;
  `LLMTurnProcessor`'s barge-in reaction and removed reset call; new `TTSProcessor`; pipeline
  builder takes the new dependencies)
- `apps/voice/app/main.py` (fetch TTS config, construct TTS provider, wire it through)
- `apps/voice/tests/test_turn_detection.py`, `test_media_session.py`, `test_provider_factory.py`
- `apps/api/app/main.py` (new internal router registration)
- `apps/api/tests/test_speech_providers.py` (new cancellation-via-task-cancel test)
- `.env`, `.env.example` (`TTS_PROVIDER`)

**Unchanged**
- `apps/api/app/api/internal/glossary.py`, `internal/turn_detection.py`, `internal/llm_config.py`,
  `internal/retrieval.py`, `internal_deps.py` - reused as-is, not modified.
- No frontend file.

## Data / contracts

**Internal tts-config response** - `{"voice_id": str, "speech_rate": float}`, always present.

**`caller_speech_started` message** - `{"type": "caller_speech_started"}`.

**`reply_finished` message** - `{"type": "reply_finished"}` - pushed by `TTSProcessor` every time
it resets the turn detector (normal completion or barge-in). Added mid-implementation: with reset
ownership living in `TTSProcessor`'s own background task rather than synchronously alongside a
message a caller already reads (as item 20d's `LLMTurnProcessor`-owned reset did), nothing -
not even a test - could otherwise tell "the LLM's reply text is done" (`llm_complete`) apart from
"the caller has actually heard all of it and a new turn can now be detected" (this).

**`tts_error` message** - `{"type": "tts_error", "text": str}` (a fixed, caller-safe apology
string per sentence, never the raw exception message).

**Audio output** - raw synthesized PCM bytes, sent as binary WebSocket messages (via the existing
`RawAudioFrameSerializer`/`OutputAudioRawFrame` path) - no new message envelope, since it is
binary, not JSON.

## Testing

The backend gate is live for both services, matching items 20c/20d's precedent throughout: pure
logic (Step 1) gets full unit coverage; the shared-mock fix and provider factory (Step 2) get
targeted tests reusing existing fixtures where possible; the internal endpoint and client
(Step 3) mirror `llm-config`'s own test shape exactly; pipeline-stage wiring (Steps 4-5) extends
the established `test_media_session.py` file; the full pipeline (Step 6) gets end-to-end
WebSocket tests with `MockSTT`/`MockLLM`/`MockTTS`/a scripted VAD fake - no real network, no real
model, no real voice, anywhere in the suite.

## Notes for the AI

- **Verify `OutputAudioRawFrame`'s constructor signature and the output transport's existing
  chunking threshold (~1280 bytes before a WebSocket flush, discovered in item 20a) still apply
  here** - Step 6's tests need audio payloads large enough to clear it, exactly like every prior
  audio-related test in this feature line.
- **Use `ws.receive()` (the generic Starlette test-session method), not `receive_text()`/
  `receive_bytes()`, when a test's message order mixes JSON and binary audio and you are not
  certain of the exact interleaving** - `receive_text()`/`receive_bytes()` assert their expected
  type and fail loudly on a mismatch rather than skipping ahead, so guessing wrong about
  interleaving order breaks the test even when the underlying behavior is correct. Verified
  empirically during this feature's own design.
- **The three-condition reset check in `TTSProcessor`** (LLM signaled done, sentence queue empty,
  nothing currently playing) **must be evaluated at both places any of the three can newly become
  true** - once when `llm_complete`/`llm_error` arrives (in case nothing was left to play), and
  again after every sentence finishes playing normally (in case completion arrived while a
  sentence was still in flight). Missing either one leaves the detector permanently latched.
  `reset_for_next_turn()` is idempotent, so evaluating it in both places is safe even if both
  happen to fire.
- **`caller_speech_started`'s cancellation must go through `asyncio.Task.cancel()` on
  `LLMTurnProcessor`'s and `TTSProcessor`'s own tracked task references** - exactly the same
  mechanism `EndFrame`/`CancelFrame` already use in both processors, not a new mechanism.
- A push, if any, at the end of this feature does not need your explicit go-ahead - the user's
  `/feature` invocation for this item included the standing "don't ask for any permission, go
  with your recommendation" override, matching items 20a, 20b, and 20d.

## Findings

Carried forward from the ledger - `closed` before this feature's own `/complete` ran, not
findings raised against this feature's own work. Each entry's own **Found**/**Resolution** lines
preserve where it actually originated and was fixed.

### 20e/F-27 [P3] closed - `_signed_in` is duplicated across five test files, byte-identical

**File:** apps/api/tests/test_permission_enforcement.py:20
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** The exact same `_signed_in(client, email)` helper is defined
identically in `test_organizations.py`, `test_organization_authorization.py`,
`test_organization_members.py`, `test_invitations.py`, and now
`test_permission_enforcement.py` (diffed all five, byte-identical). Four predate
this feature - pre-existing drift from feature 2 that its own three audit rounds
never caught - and this feature added a fifth rather than breaking the pattern.
Not a defect; every copy works. It is compounding maintainability debt: a change
to the registration payload shape now needs five identical edits, and each new
test file makes the eventual extraction slightly more work.
**Suggested fix:** Move `_signed_in` into `conftest.py` as a shared helper or
fixture, import it from all five files. While in that territory, the three
differently-named "create an org and add a member" helpers
(`_org_with_role`, `_org_with_owner`, `_org_with_second_member`) are a related,
looser instance of the same pattern - worth a look in the same pass, though their
differing return shapes mean the fix isn't as mechanical.
**Resolution:** Fixed in feature 4a, Step 1. `_signed_in` and the two byte-identical
`_org_with_owner` copies (`test_invitations.py`, `test_organization_members.py`)
moved into `tests/conftest.py`; all five `_signed_in` sites and both
`_org_with_owner` sites now import the shared version. `_org_with_role`
(`test_permission_enforcement.py`) and `_org_with_second_member`
(`test_organization_authorization.py`) were deliberately left in place - their
differing return shapes (2-tuple vs. 4-tuple) mean a forced merge risks a subtle
bug in two already-correct files for a P3 finding's marginal benefit. The
unrelated DB-level `_org_with_owner` in `test_organization_concurrency.py` was
also left alone; it builds fixtures directly, not through the API. Re-reviewed
2026-08-27 (scope: full; lens: quality): `conftest.py` and the five originally
affected test files are unchanged since the fix, confirmed zero duplicate
`_signed_in` definitions outside `conftest.py`. Closed.

### 20e/F-29 [P2] closed - Workspace `settings` update has zero test coverage

**File:** apps/api/tests/test_workspaces.py
**Found:** 2026-08-27 by /audit (scope: current; lens: tests)
**Why it matters:** `WorkspaceUpdate.settings` and `workspace_repo.update`'s partial-update
handling of it are live, mutable code paths, but no test in `test_workspaces.py` ever sends
`settings` in a PATCH request. The only reference to `settings` in the whole file is the
create-test's default-`{}` assertion. The sibling resource, organizations, has direct
coverage of this exact pattern (`test_organization_members.py::test_update_settings_without_touching_name`).
The underlying code is a structural copy of `organization_repo.update` (already proven correct),
so this is a coverage gap rather than a suspected bug - hence P2, not P1.
**Suggested fix:** Add a test that PATCHes `settings` on a workspace and asserts it persists,
and (mirroring the organization test) a test proving a name-only update leaves `settings`
untouched, and a settings-only update leaves `name` untouched.
**Resolution:** Fixed, then re-reviewed 2026-08-27 (scope: apps/api item-6 files; lens: tests).
`test_update_settings_without_touching_name` and `test_update_name_without_touching_settings`
in `test_workspaces.py` both pass, correctly assert `settings` persists on a settings-only PATCH
and stays untouched on a name-only PATCH (and vice versa) - the exact partial-update semantic
`workspace_repo.update` implements. No new defect introduced. Closed.

### 20e/F-31 [P2] closed - No test proves a WorkspaceMember grant to one workspace doesn't leak access to a sibling workspace

**File:** apps/api/tests/test_workspaces.py
**Found:** 2026-08-27 by /audit (scope: apps/api item-6 files; lens: tests)
**Why it matters:** `require_workspace_access` and `workspace_repo.list_for_user` both scope
the `WorkspaceMember` check to the exact `workspace_id` in play (`WHERE workspace_id = :id AND
user_id = :id`), so a member granted access to workspace A should not be able to `GET` or see
in the list a sibling workspace B in the same organization. The query logic is correct on
inspection, but nothing tests it: the existing coverage only proves "zero memberships -> empty
list / 404" and "the one membership that matches -> access granted," never "a membership that
exists but doesn't match." This is exactly the kind of tenant/resource-boundary case this
project otherwise tests explicitly (see `test_tenant_isolation.py` for the equivalent at the
organization level). Not a proven bug - hence P2, not P1.
**Suggested fix:** Add a test that inserts a `WorkspaceMember` row for workspace A (same
technique F-29's sibling tests and the existing `test_get_succeeds_for_an_explicit_member`
already use, since 6a has no member-add endpoint yet) and asserts that member gets 404 on
`GET` for workspace B, and that workspace B does not appear in their `list` results.
**Resolution:** Fixed. Added `test_member_access_to_one_workspace_does_not_reach_a_sibling` to
`test_workspaces.py`, verified it actually catches the regression by temporarily dropping the
`workspace_id` filter in `workspace_member_repo.get` (the test failed as expected: 200 instead
of 404), then reverted that change cleanly. Re-reviewed 2026-08-27 (scope: current, feature 6b;
lens: tests): `workspace_member_repo.get`'s body is unchanged since the fix (confirmed via diff),
the test still passes in the full suite, and 6b's own new `get_by_id`/`list_for_workspace` follow
the identical workspace-scoping discipline (verified `remove_member`'s cross-workspace 404 test
exercises it too). No new defect introduced. Closed.
