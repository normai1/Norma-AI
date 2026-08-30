# Feature: Turn detection

**From build-plan:** feature 20c

**Status:** complete

## Goal

Decide when a caller has actually finished speaking - the pipeline stage between streaming STT
(20b) and the LLM turn loop (20d, unbuilt): "Voice activity detection (Silero-class VAD) for
speech boundaries, plus semantic end-of-turn classification on top, with operator-configurable
sensitivity" (`project-overview.md`). `AssistantVersion.turn_sensitivity` (item 11b, already a
locked, validated `0.0-1.0` field) is the exact "operator-configurable sensitivity" this feature
wires up its first real consumer for.

## Design reference

None. Backend-only.

## Architecture decisions (read before building)

- **VAD is not a swappable provider, unlike STT/TTS/LLM/Telephony.** CLAUDE.md section 8's
  provider list has no `VADProvider` - `project-overview.md` names a specific technique (Silero)
  directly, not "one of several vendor options." Silero VAD ships inside the already-installed
  `pipecat-ai` package (`pipecat.audio.vad.silero.SileroVADAnalyzer`, confirmed installed and
  loadable) and needs no vendor account, API key, or swap path. Using it directly, not wrapped in
  a Norma provider abstraction, is consistent with this feature - not a shortcut around item
  20a/20b's "behind Norma's own interfaces" reasoning, which was specifically about vendor STT/
  TTS APIs Norma must be able to swap.
- **The decision logic is a pure, directly-unit-testable module - the Pipecat pipeline is a thin
  adapter around it**, mirroring items 17-19's `chunker.py`/`context_builder.py` precedent. Every
  turn-ending decision (VAD state + latest transcript + sensitivity -> ended or not) is testable
  with plain function calls, no WebSocket/Pipecat harness required. Only the wiring into a live
  `FrameProcessor` needs the heavier end-to-end test.
- **`SpeechToTextProcessor` (20b) changes: it now forwards `InputAudioRawFrame` downstream
  instead of consuming it.** 20b's own docstring said "nothing downstream needs raw audio once
  STT has it" - true when STT was the last pipeline stage. It no longer is: turn detection needs
  the same raw audio (for VAD) that STT needs (for transcription). This is a deliberate revision
  of that stated design, not an oversight - documented here since it reverses a explicit prior
  decision.
- **A new internal endpoint, not an extension of item 20b's glossary one.** `GET .../glossary`
  is scoped to exactly what STT keyword biasing needs; folding an unrelated float into its
  response would blur that scope for a trivial, non-latency-critical saving (this value is
  fetched once at session setup, not per turn, so an extra internal round trip here costs nothing
  that matters). A second small, clearly-named endpoint keeps both concerns crisp.
- **"Semantic end-of-turn classification" is a small, explicit heuristic, not an ML classifier.**
  Building or hosting a real semantic-completeness model is out of scope for an MVP spike - CLAUDE.md's
  own guidance against a frontier model in the per-turn loop applies in spirit here too: nothing
  today justifies the latency/cost of a model call just to decide "did that sentence sound
  finished." The heuristic (does the final transcript end in terminal punctuation, or a small set
  of obvious continuation words like "and"/"but"/"so") is honestly labeled as a placeholder for
  real semantic modeling later, matching the project's established pattern of flagging simplified
  logic rather than silently presenting it as more sophisticated than it is.
- **A hard fallback timeout prevents indefinite waiting.** If VAD-detected silence persists well
  beyond the sensitivity-derived threshold even though the semantic heuristic says "incomplete,"
  the turn ends anyway. CLAUDE.md's "must not leave dead air" requirement applies to a stalled
  turn-detection decision exactly as much as to a stalled provider call.
- **Sensitivity maps to VAD's `stop_secs`** (how long silence must persist before VAD reports
  stopped speaking) - `0.0` sensitivity -> the most patient threshold (`1.5s`), `1.0` -> the most
  eager (`0.3s`), matching `VADParams`' own existing shape (confirmed real: `confidence`,
  `start_secs`, `stop_secs`, `min_volume`, defaults `0.7`/`0.2`/`0.2`/`0.6`).
- **The hard fallback timeout is a fixed constant, `FALLBACK_TIMEOUT_SECONDS = 3.0`** - roughly
  double the most patient `stop_secs` (`1.5s`), giving a semantically-incomplete transcript real
  extra grace before the turn is forced to end anyway, without ever leaving dead air anywhere
  close to how long a caller would perceive as a hang. Measured from the moment VAD first reports
  sustained silence, not from turn start.
- **`VADAnalyzer.analyze_audio(buffer: bytes) -> VADState` is `async`, and already buffers
  arbitrary-sized input internally** - confirmed by reading `VADAnalyzer._run_analyzer`'s actual
  source during 20c research: it appends every call's bytes to its own internal buffer and only
  runs the model once `num_frames_required()` samples (`256` = `512` bytes at 16kHz/16-bit mono)
  have accumulated, carrying any remainder forward itself. `TurnDetector.feed_audio` therefore
  does **not** need its own re-chunking buffer - an earlier draft of this spec assumed it would
  and was wrong; call `analyze_audio` directly with whatever size chunk arrives.
- **The VAD analyzer's own state machine already tracks `stop_secs` timing** - it reports
  `VADState.STOPPING` immediately on the first silent frame after `SPEAKING`, then
  `VADState.QUIET` only once enough silent frames have accumulated to cover `stop_secs`. Detecting
  "VAD-detected sustained silence" is therefore just: after having observed `SPEAKING` at least
  once, the state returns to `QUIET`. `TurnDetector` does not need to re-implement this timing
  itself - only the separate hard fallback timeout (below) needs its own wall-clock tracking.
- **`SileroVADAnalyzer`'s sample rate must be set via an explicit `set_sample_rate(hz)` call
  after construction** - confirmed empirically: passing `sample_rate=` to the constructor stores
  it as a fallback (`_init_sample_rate`) but leaves the analyzer's active `sample_rate` at `0`
  until `set_sample_rate()` actually runs, which also finalizes the `stop_secs`/`start_secs`
  frame-count thresholds from `VADParams`. `TurnDetector`'s constructor must call
  `vad_analyzer.set_sample_rate(sample_rate)` itself rather than trusting the constructor kwarg.

## In scope

- **`apps/voice/app/turn_detection.py`** - pure logic:
  - `sensitivity_to_stop_secs(sensitivity: float) -> float` - the mapping above.
  - `is_semantically_complete(text: str) -> bool` - the heuristic above.
  - `TurnDetector` - a small state machine constructed with a `sensitivity: float`, an injectable
    `vad_analyzer` (defaults to a real `SileroVADAnalyzer`, configured with
    `VADParams(stop_secs=sensitivity_to_stop_secs(sensitivity))` and explicitly
    `set_sample_rate`-initialized; injectable with a scripted fake for tests so the real model is
    never loaded there), and an injectable `clock: Callable[[], float]` (defaults to
    `time.monotonic`, injectable for deterministic fallback-timeout tests). Exposes
    `async def feed_audio(chunk: bytes) -> None` (calls `vad_analyzer.analyze_audio` directly -
    no re-chunking needed, see Architecture decisions - and tracks the SPEAKING-then-QUIET
    transition as sustained silence) and `feed_transcript(text: str, *, is_final: bool) -> None`
    (only a final transcript updates the text the semantic check runs against; a partial
    transcript is recorded but never itself ends a turn), and `turn_ended() -> bool` reflecting
    the combined decision - sustained silence AND the latest final transcript passes the semantic
    check, OR `FALLBACK_TIMEOUT_SECONDS` has elapsed since sustained silence began.
- **`GET /internal/v1/assistants/{assistant_id}/turn-detection-config`** (new, `apps/api`) -
  `{"sensitivity": float}`, resolved from the assistant's current `AssistantVersion` (`0.5` -
  the schema's own default - if the assistant has never been published, i.e.
  `current_version_id` is `None`; a session should be testable before formal publishing).
  Same `RequireInternalSecret` auth as the glossary endpoint (item 20b).
- **`apps/voice` client** - a small `httpx`-based fetch function mirroring
  `glossary_client.fetch_glossary_terms`'s exact shape (injectable client, fails open - to the
  schema default `0.5`, not a made-up fallback - on any error).
- **`SpeechToTextProcessor` revision** - forwards `InputAudioRawFrame` downstream after queuing
  it for STT, rather than consuming it.
- **`TurnDetectionProcessor`** (new Pipecat `FrameProcessor`, `apps/voice/app/media_session.py`)
  - wraps a `TurnDetector`, placed immediately after `SpeechToTextProcessor` in the pipeline.
  Feeds forwarded `InputAudioRawFrame`s and observes the transcript
  `OutputTransportMessageUrgentFrame`s flowing past it (both directions of "past" - audio flows
  in from upstream, transcripts flow in from upstream too, since `SpeechToTextProcessor` sits
  before this stage). Emits its own `{"type": "turn_ended", "text": ...}` message when
  `TurnDetector.turn_ended()` becomes true.
- **`/media/session` wiring** - fetches sensitivity alongside glossary terms at session setup,
  passes it into the extended pipeline builder.
- **End-to-end test** - audio simulating speech then silence, with a final transcript in between,
  produces a `turn_ended` JSON message after the sensitivity-derived pause - using an injected
  fake VAD analyzer (not the real Silero model) so the test is fast and deterministic, matching
  `MockSTT`'s own precedent of never depending on a real model/vendor for test determinism.

## Out of scope

- **The LLM turn loop that actually acts on a completed turn.** Item 20d - `turn_ended` is
  observed as a JSON message over the test WebSocket, not yet handed to a model.
- **Barge-in (caller speech interrupting assistant playback).** Item 20e - there is no assistant
  playback yet for anything to interrupt.
- **A real semantic-completeness model.** Explicitly named above; the heuristic is a documented
  placeholder.
- **Per-turn latency instrumentation / `TurnMetric` rows.** Item 20f.
- **Tuning `sensitivity_to_stop_secs`'s exact endpoints against real call data.** `0.3s`/`1.5s`
  are reasonable starting values, not a tuned product decision - item 61's real load/latency
  validation is where this gets revisited against production data.
- **Exposing the operator-facing `turn_sensitivity` setting in any UI.** Item 11d (assistant
  editor UI) already owns that; this feature only consumes the value the API already stores.

## Build steps

- [x] **Step 1 - pure turn-detection logic** - `apps/voice/app/turn_detection.py`
  (`sensitivity_to_stop_secs`, `is_semantically_complete`, `TurnDetector`).
  *Done when:* `apps/voice/tests/test_turn_detection.py` proves: sensitivity mapping is
  monotonic and bounded correctly at `0.0`/`1.0`; the semantic heuristic accepts
  punctuation-terminated text and rejects the continuation-word list; `TurnDetector` (with an
  injected fake VAD analyzer, not the real Silero model) reports `turn_ended()` only after both
  sustained silence and a semantically-complete final transcript; a semantically-incomplete final
  transcript does not end the turn immediately, but the hard fallback timeout still ends it
  eventually. Full `apps/voice` suite green. `ruff check apps/voice` clean.

- [x] **Step 2 - internal turn-detection-config endpoint + `apps/voice` client** - `apps/api`
  gets `app/repositories/assistant_version.py::get_by_id`, the new internal route; `apps/voice`
  gets `app/turn_detection_client.py`.
  *Done when:* `apps/api` gets `tests/test_internal_turn_detection_config.py` - returns the
  current version's `turn_sensitivity` for a published assistant, `0.5` for an unpublished one
  (`current_version_id is None`), 404 for an unknown assistant, 401 without the secret header.
  `apps/voice` gets `tests/test_turn_detection_client.py` proving a successful fetch, a non-200
  response, and a connection failure all resolve to `0.5` (fake `httpx` transport, no real
  network call). Full `apps/api` suite still green. `ruff check` clean on both services.

- [x] **Step 3 - pipeline wiring and end-to-end test** - `SpeechToTextProcessor` forwards audio;
  `TurnDetectionProcessor` (new) added to `media_session.py`; `/media/session` fetches
  sensitivity and wires the extended pipeline.
  *Done when:* the WebSocket test proves a `turn_ended` message arrives, using a fake VAD
  analyzer for determinism (matching Step 1's own precedent - no real Silero model in the test
  suite). Full `apps/voice` suite green. `ruff check apps/voice` clean. `docker compose build
  voice && docker compose up -d voice` succeeds; `/health` still 200.

## Files / areas

**New**
- `apps/voice/app/turn_detection.py`, `app/turn_detection_client.py`
- `apps/voice/tests/test_turn_detection.py`, `test_turn_detection_client.py`
- `apps/api/app/api/internal/turn_detection.py`
- `apps/api/tests/test_internal_turn_detection_config.py`

**Modified**
- `apps/api/app/repositories/assistant_version.py` (`get_by_id`)
- `apps/api/app/main.py` (new internal router registration)
- `apps/voice/app/media_session.py` (`SpeechToTextProcessor` forwards audio; new
  `TurnDetectionProcessor`; pipeline builder renamed `build_voice_session_pipeline_worker` and
  now also constructs the `TurnDetector`)
- `apps/voice/app/main.py` (fetch sensitivity, wire it through)
- `apps/voice/tests/test_media_session.py` (extended for the new stage)

**Unchanged**
- `apps/api/app/api/internal/glossary.py`, `app/api/internal_deps.py` - reused as-is, not
  modified.
- No frontend file.

## Data / contracts

**Internal turn-detection-config response** - `{"sensitivity": float}`, always present (defaults
to `0.5` rather than omitting the key when unpublished).

**Turn-ended message** - `{"type": "turn_ended", "text": str}` - the semantically-complete final
transcript that ended the turn.

## Testing

`apps/voice` full suite: 27 passed. `apps/api` full suite: 554 passed. `ruff check` clean on both
services. `docker compose build voice` and `docker compose up -d voice` succeeded; `/health`
returned `{"status":"ok","active_sessions":0,"capacity":10}`.

## Notes from the build

- A pre-implementation correction: the draft spec assumed `TurnDetector.feed_audio` would need
  its own re-chunking buffer to match `SileroVADAnalyzer`'s fixed 512-byte frame requirement.
  Live-inspecting `VADAnalyzer`'s actual source before writing code showed `analyze_audio` is
  `async` and already buffers arbitrary-sized input internally, and that its own state machine
  already times `stop_secs`. The spec was corrected before any code was written against the wrong
  assumption.
- `build_speech_to_text_pipeline_worker` was renamed to `build_voice_session_pipeline_worker`
  since it now also owns turn detection, not just STT.
- The two existing `test_media_session.py` tests from item 20b needed updating alongside the new
  test: without mocking `fetch_turn_sensitivity` and injecting a fake VAD analyzer, they would
  have made an unmocked network call and loaded the real Silero model on every run.
