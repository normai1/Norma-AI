# Feature: Voice pipeline test harness

**From build-plan:** feature 22

**Status:** complete

## Goal

Give the voice pipeline test suite a reusable "conversation replay" harness, closing the real
gap left after items 20b-20g: every one of the 25 tests in `test_media_session.py` already
proves fixture-audio-driven, mock-provider-backed pipeline behavior (partial/final transcripts,
turn detection, LLM streaming, barge-in cancellation, sentence-chunked TTS, provider retry and
failover, turn metrics, ticket rejection) - CLAUDE.md section 28's "Voice pipeline tests" tier is
already substantively built, just never assembled into the reusable, discoverable form CLAUDE.md
section 42 calls a "harness." Every one of those 25 tests hand-wires the same ~15 lines of
provider monkeypatching, VAD scripting, and WebSocket setup inline. This feature extracts that
into a small, documented, reusable module and uses it to add the two dimensions of coverage that
are still genuinely missing: a true multi-turn conversation with a mid-reply interruption
(existing barge-in tests prove the internal cancellation mechanics, not "the caller gets a real
answer to what they actually asked after interrupting"), and an end-to-end (not just unit-level)
proof of the turn-detector's fallback timeout, which today is only exercised against a bare
`TurnDetector` instance in `test_turn_detection.py`, never through the real WebSocket pipeline.

## Design reference

None. Backend test infrastructure only.

## Architecture decisions (read before building)

- **The harness is additive, not a rewrite.** The 25 existing tests in `test_media_session.py`,
  the 16 in `test_turn_detection.py`, and `test_latency_regression.py`/`test_session_resilience.py`
  stay exactly as they are - each one is already a hard-won, individually-debugged proof of a
  specific pipeline behavior (several were how real bugs got caught during items 20e-20g).
  Retrofitting them onto the new harness would be a large, purely-cosmetic diff with real
  regression risk and zero new coverage. The harness is new, separate infrastructure that new
  tests can build on; it proves itself by being used for the two genuinely new tests this feature
  adds, not by replacing what already works.
- **`apps/voice/tests/conversation_harness.py` (new) builds directly on `conftest.py`'s existing
  pieces** (`_patch_session_setup`, `_ScriptedVADAnalyzer`, `_patch_turn_detector_vad`,
  `_media_session_url`, `_receive_one`, `_fake_fetch_retrieved_context`) rather than duplicating
  them. Living in its own file makes it the one discoverable entry point item 22 asks for, with a
  module docstring that also points at where the rest of the pipeline's behavioral coverage
  already lives.
- **`receive_until(ws, stop_types, limit)` is bounded and fails loudly, not silently.** A
  fixture-driven test does not know in advance exactly how many messages a scenario produces (that
  was `test_media_session.py`'s own approach - hard-coded counts). A reusable harness instead
  collects messages until one whose `type` is in `stop_types` arrives, raising `AssertionError`
  with the partial trace if a hard `limit` (default 50) is hit first.
- **The turn-detector's `FALLBACK_TIMEOUT_SECONDS` (3.0s, `app/turn_detection.py`) needed a fake
  clock to test through the real pipeline without a real 3+ second sleep - no production code
  change was actually needed to get it.** `TurnDetector` already accepts an injectable `clock:
  Callable[[], float]`, and `conftest.py`'s existing `_patch_turn_detector_vad` already
  monkeypatches `media_session_module.TurnDetector` itself (wrapping its constructor to inject a
  scripted VAD analyzer) rather than threading a parameter through
  `build_voice_session_pipeline_worker`. The spec originally planned a `clock` keyword argument on
  `build_voice_session_pipeline_worker`; during implementation this turned out unnecessary once
  the existing constructor-wrapping technique was checked first - `_patch_turn_detector_vad`
  gained an optional `clock` keyword instead (injecting both together, avoiding any risk of two
  separate monkeypatches clobbering each other), confirmed by a clean `git diff` on
  `apps/voice/app/` after reverting the originally-planned production change. `media_session.py`
  has zero changes in this feature.
- **The interruption-conversation test is genuinely new coverage, not a rewrite of an existing
  barge-in test.** `test_media_session_cancels_an_in_flight_llm_call_on_caller_speech_started`
  already proves the internal mechanics (the interrupted call is cancelled, a second turn is
  detected) but stops there. This proves the caller-visible outcome: after interrupting with a
  different question, the caller gets a real, complete answer to what they actually asked the
  second time - proven via the LLM's most recent call having received the caller's real second
  question as its own latest message, since MockLLM returns the same scripted response text
  regardless of input and conversation history legitimately still contains the interrupted first
  question (barge-in cancels the reply, not the fact that the caller said something).

## In scope

- **`apps/voice/tests/conversation_harness.py`** (new) - `open_conversation_session()` (a context
  manager wrapping provider monkeypatching + VAD/clock scripting + `TestClient`/`websocket_connect`
  setup), `send_audio_chunks(ws, count, chunk=None)`, `receive_until(ws, stop_types, limit=50)`.
- **`apps/voice/tests/conftest.py`** - `_patch_turn_detector_vad` gained an optional `clock`
  keyword argument, injected via the same constructor-wrapping technique as `vad_analyzer`.
- **`apps/voice/tests/test_conversation_replay.py`** (new) - three tests using the harness: a
  simple single-turn Q&A (demonstrates the harness), the interruption conversation (genuinely new
  coverage), and the end-to-end fallback-timeout scenario (genuinely new coverage).

## Out of scope

- Rewriting or retrofitting any of the existing 25/16/1/4 tests onto the new harness.
- Tool-permission tests (no in-call skills exist yet).
- "Exhausted minutes" failure-mode tests (no billing/metering exists yet).
- Real audio waveform fixtures (STT is mocked end-to-end by design; "fixture-audio" here means
  scripted transcript events paced against audio-chunk delivery).
- A CI dashboard, coverage report, or new `Verify` command wiring.

## Build steps

- [x] **Step 1 - the harness module, proven by one demonstration test** - built as specced.
  `apps/voice` suite: 105/105 (104 pre-existing + 1 new). `ruff check apps/voice` clean.

- [x] **Step 2 - the interruption conversation** - built as specced, asserting on
  `mock_llm.received_messages[-1]` (the LLM's most recent call) rather than "not in" the full
  message list, since conversation history legitimately retains the interrupted first question.
  106/106. `ruff check apps/voice` clean.

- [x] **Step 3 - the end-to-end fallback-timeout scenario** - built with a smaller footprint than
  specced: no `media_session.py` change, just an optional `clock` keyword on the existing
  `_patch_turn_detector_vad` helper (see Architecture decisions). 107/107, ~11s total suite time
  (confirming no real sleep occurred). `ruff check apps/voice` clean. `git diff --stat
  apps/voice/app/` empty, confirming zero production code changes across the whole feature.

## Files / areas

**New**
- `apps/voice/tests/conversation_harness.py`
- `apps/voice/tests/test_conversation_replay.py`

**Modified**
- `apps/voice/tests/conftest.py` (`_patch_turn_detector_vad` gained an optional `clock` kwarg)

**Unchanged**
- `apps/voice/app/media_session.py` and every other production file - zero production code
  changes in this feature.
- Every existing test file under `apps/voice/tests/` other than `conftest.py`'s one addition.
- `apps/api` - nothing in this feature touches the control plane.

## Data / contracts

No new API, WebSocket message, or stored shape. The harness functions are test-only.

## Testing

This feature *is* test infrastructure. Final state: 107/107 `apps/voice` tests passing (104
pre-existing + 3 new), `ruff check apps/voice` clean, zero production code changes.

## Notes for the AI

- `receive_until`'s `stop_types` should state exactly what event the scenario is waiting for, not
  collect indiscriminately.
- The interruption test asserts on the *content* of the second answer's context
  (`received_messages[-1]`), not just that a second `llm_complete` arrived.
- The fallback-timeout test uses a simple `_FakeClock` (mirrors `test_turn_detection.py`'s own),
  not a real `asyncio.sleep`.
- **Before adding a parameter to production code purely for test injectability, check whether an
  existing constructor-wrapping monkeypatch (like `_patch_turn_detector_vad`) can already reach
  it.** This feature's spec originally planned a `media_session.py` change for clock injection;
  checking first avoided it entirely.

## Findings

None recorded against this feature; the ledger's outstanding entries all predate it and are
unrelated.
