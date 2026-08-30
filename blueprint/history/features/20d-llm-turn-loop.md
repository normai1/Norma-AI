# Feature: LLM turn loop

**From build-plan:** feature 20d

**Status:** complete

## Goal

Close the loop from a detected turn (20c) to a spoken-ready reply: realtime LLM streaming,
assistant-configuration-plus-retrieved-context assembly, and in-session conversation state - the
three things build-plan item 20d names explicitly. This is still a proof stage, not a finished
call: the reply arrives as streamed JSON text over the test WebSocket, since sentence-chunked TTS
playback and barge-in are item 20e's job.

## Design reference

None. Backend-only.

## Architecture decisions

- **The LLM provider abstraction is new and lives in `apps/voice`, not `norma_shared`.** CLAUDE.md
  section 8 lists `LLMProvider — realtime and post-call tiers` as one conceptual abstraction, but
  only the realtime tier has a real consumer today - `apps/worker` (post-call summaries, item 35)
  is an empty scaffold with no LLM need yet. Matching item 20b's own stated migration rule ("moved
  here, not duplicated, when a real cross-service need first arises"), this stays in `apps/voice`
  until a second consumer actually exists; a future item 35 promoting it to `norma_shared` is
  expected, not a surprise.
- **Realtime model: `claude-haiku-4-5-20251001`.** CLAUDE.md section 8.1 requires "claude-haiku-4-5
  class" in the per-turn loop and explicitly rejects a frontier model there. Configurable via
  `LLM_REALTIME_MODEL`, never hard-coded into a route or processor.
- **Context assembly happens in `apps/voice`, not `apps/api`.** The build-plan line names "context
  assembly" as this feature's own job, and CLAUDE.md's pipeline diagram places it as its own stage
  between retrieval and the realtime LLM call. `apps/api`'s two new internal endpoints each return
  one finished piece - a system-prompt string, a retrieved-context string - and `apps/voice`
  combines them with conversation history. This mirrors item 20c's split exactly: business logic
  (rendering, embedding, pgvector search) stays in `apps/api`; assembly of the final per-turn
  prompt happens where the turn loop runs.
- **Two new internal endpoints, not one.** `GET .../llm-config` (resolved once at session
  setup - an assistant's prompt/persona/creativity do not change mid-call) and `POST .../retrieve`
  (called once per turn, since the query changes every time). Folding both into one endpoint would
  force a network round trip for static configuration on every single turn for no benefit.
- **Prompt-template rendering failure fails open to `persona`, not a 500.** `render_prompt`
  (item 12b) raises `PromptRenderError` for a template referencing an unknown placeholder - a real
  authoring bug, correctly loud in the prompt-editor UI (item 12c). But a live call is not where
  CLAUDE.md's "silence is the worst possible failure" allows an authoring bug to drop the call:
  the internal `llm-config` endpoint catches `PromptRenderError` and falls back to the
  assistant's plain `persona` field, then to a fixed default if even that is unset.
- **`retrieve()`'s and `build_context()`'s existing tenant/workspace scoping is reused as-is.**
  The new internal `/retrieve` endpoint is a thin composition of two already-tested pure/service
  functions (item 19) - it adds no new retrieval logic, just resolves `assistant_id` to its
  `organization_id`/`workspace_id` first, exactly like the existing internal glossary and
  turn-detection-config endpoints already do.
- **`TurnDetector` needs to detect more than one turn per call - a real gap in item 20c's design,
  found while scoping this feature.** 20c's `turn_ended()` latches `True` forever once set, which
  was correct for proving one turn-ended event but cannot support "conversation state" (this
  feature's own explicit scope), which requires detecting a second, third, ... caller turn in the
  same session. `TurnDetector` gains `reset_for_next_turn()`, clearing the ended/silence/speaking
  state **and** `last_final_transcript` - the caller (`TurnDetectionProcessor`) must read
  `last_final_transcript` before calling reset, since leaving stale text in place would let a
  burst of silence at the start of the next turn reuse the previous turn's already-complete
  sentence and end the new turn instantly, before any new transcript arrives. Found and corrected
  during implementation, not left as originally drafted.
- **Only one LLM turn ever runs at a time - by construction, not a race resolved at runtime.**
  `reset_for_next_turn()` runs only inside `LLMTurnProcessor`'s own `finally` block once a call
  finishes, and `TurnDetector.turn_ended()` stays latched `True` (so `TurnDetectionProcessor`
  cannot emit a second `turn_ended`) until that reset happens - found and reasoned through while
  wiring Step 5, after first assuming a separate "drop while in flight" runtime race would need
  its own test. `LLMTurnProcessor` still keeps a cheap is-a-call-already-running check as a
  defensive invariant (free today, and item 20e's barge-in work is a plausible future change to
  that sequencing), but it is not independently reachable through the pipeline as currently wired
  - so it is not the thing this feature's tests exercise for multi-turn correctness. What the
  tests exercise instead is the real, reachable claim: a second, independent turn (a fresh
  speak-silence-complete cycle after the first reply finishes) is correctly detected and answered.
- **A second real gap found while wiring Step 5, in `TurnDetectionProcessor` (item 20c) itself.**
  Its `_turn_ended_emitted` was a permanent one-shot latch - correct when only one turn ever
  needed proving, but it would silently block every turn after the first now that resets exist.
  Fixed to edge-trigger off `TurnDetector.turn_ended()`'s own False->True transition instead of a
  separately-tracked flag, so it re-arms automatically the moment the shared detector resets, with
  no direct reference needed between the two processors.
- **An LLM provider failure emits one fallback message, not a retry.** CLAUDE.md's "provider
  failure never produces silence" is non-negotiable even at spike stage; a full retry/failover
  policy is item 20g's job by name ("Session resilience"). This feature catches `LLMProviderError`
  during streaming and pushes a single `{"type": "llm_error", ...}` message so nothing is left
  silent, then stops - no retry.
- **The retrieved-context framing includes one line treating it as data, not instructions** -
  `assemble_system_prompt` wraps retrieved text under a heading that says so explicitly. This is
  not item 47's guardrail system (topic allow-lists, output validation, injection-resistance
  testing are all explicitly out of scope there); it is simply how the context gets worded into
  the prompt, and costs nothing to get right now rather than never.
- **A spoken greeting is still impossible after this feature** - greeting playback needs TTS
  (20e). This feature does not attempt to synthesize or send the greeting text anywhere.
- **`creativity` (bounded temperature) rides along with the system prompt.** CLAUDE.md's
  assistant-configuration section names "creativity (bounded temperature)" as one of the
  operator-configurable settings, and the build-plan line's own "assistant configuration...
  assembly" wording covers it just as much as the prompt text. It varies per assistant (unlike
  the model, which is fixed per tier), so it is a per-call `LLMProvider.stream()` argument, not
  bound at construction - the same distinction `ElevenLabsTTS.synthesize`'s per-call `voice_id`
  already draws against its construction-time `model_id`. The internal endpoint therefore returns
  both `system_prompt` and `creativity` together (one config fetch, not two), and is named
  `.../llm-config` rather than `.../system-prompt` to say so honestly.
- **An in-flight LLM background task is cancelled on `EndFrame`/`CancelFrame`.** Unlike
  `SpeechToTextProcessor`'s stream task, which ends naturally when its audio queue receives a
  sentinel, an LLM call has no such natural termination tied to an incoming frame - if the caller
  disconnects mid-response, nothing else would stop it from continuing to run and attempting to
  push frames into a torn-down pipeline. `LLMTurnProcessor` tracks its current task and cancels it
  on teardown.

## In scope

- `apps/voice/app/turn_detection.py` - `TurnDetector.reset_for_next_turn()`.
- `apps/voice/app/conversation.py` - `Message`, `ConversationState`, `assemble_system_prompt`.
- `apps/voice/app/llm.py` - `LLMProvider` protocol, error hierarchy, `Message` re-export.
- `apps/voice/app/anthropic_llm.py` - `AnthropicLLM`, verified against the real `anthropic` SDK's
  streaming API.
- `apps/voice/app/mock_llm.py` - `MockLLM`.
- `apps/voice/app/llm_provider_factory.py` - `get_llm_provider`.
- `apps/voice/app/config.py` - `LLM_PROVIDER`, `LLM_REALTIME_MODEL`, `ANTHROPIC_API_KEY`,
  `ANTHROPIC_BASE_URL`.
- `apps/api/app/services/llm_config.py` - `resolve_llm_config`.
- `apps/api/app/api/internal/llm_config.py` - `GET .../llm-config`.
- `apps/api/app/api/internal/retrieval.py` - `POST .../retrieve`.
- `apps/voice/app/llm_config_client.py`, `app/retrieval_client.py`.
- `apps/voice/app/media_session.py` - `LLMTurnProcessor`, and the `TurnDetectionProcessor`
  edge-trigger fix.
- End-to-end tests: a happy-path turn, an LLM-failure turn, and a genuine second independent turn.

## Out of scope

- Sentence-chunked streaming TTS and playback of the LLM's reply (item 20e).
- Barge-in (item 20e).
- Spoken greeting on call start (requires TTS, item 20e).
- Per-turn latency instrumentation / `TurnMetric` rows (item 20f).
- Retry, timeout-driven failover, or forwarding on LLM failure (item 20g).
- Full AI guardrails (item 47).
- Tool calls / in-call skills (item 13's own build-plan items).
- Persisting conversation turns to the database (item 22).
- Caller identity in the render context (items 23+).
- Cost/usage capture for LLM calls (item 21).

## Build steps

- [x] Step 1 - pure logic: multi-turn detection reset and conversation assembly.
- [x] Step 2 - LLM provider abstraction (mock + Anthropic + factory).
- [x] Step 3 - internal `llm-config` and `retrieve` endpoints in `apps/api`.
- [x] Step 4 - `apps/voice` clients for both endpoints.
- [x] Step 5 - pipeline wiring (`LLMTurnProcessor`) and end-to-end tests.

## Files / areas

**New**
- `apps/voice/app/conversation.py`, `app/llm.py`, `app/mock_llm.py`, `app/anthropic_llm.py`,
  `app/llm_provider_factory.py`, `app/llm_config_client.py`, `app/retrieval_client.py`
- `apps/voice/tests/test_conversation.py`, `test_llm.py`, `test_anthropic_llm.py`,
  `test_llm_config_client.py`, `test_retrieval_client.py`
- `apps/api/app/services/llm_config.py`, `app/api/internal/llm_config.py`,
  `app/api/internal/retrieval.py`
- `apps/api/tests/test_llm_config.py`, `test_internal_llm_config.py`, `test_internal_retrieval.py`
- `apps/voice/.dockerignore`

**Modified**
- `apps/voice/app/turn_detection.py` (`reset_for_next_turn`)
- `apps/voice/app/media_session.py` (`LLMTurnProcessor`; `TurnDetectionProcessor` edge-trigger fix;
  pipeline builder takes the new dependencies)
- `apps/voice/app/main.py` (fetch LLM config, construct LLM provider, wire it through)
- `apps/voice/app/config.py`, `requirements.txt`
- `apps/voice/tests/test_media_session.py` (three new end-to-end tests, three existing ones
  updated)
- `apps/api/app/main.py` (two new internal router registrations)
- `.env`, `.env.example` (`LLM_PROVIDER`, `LLM_REALTIME_MODEL`, `ANTHROPIC_BASE_URL`)

**Unchanged**
- `apps/api/app/services/retrieval.py`, `context_builder.py`, `prompt_rendering.py`.
- `apps/api/app/api/internal/glossary.py`, `internal/turn_detection.py`, `internal_deps.py`.
- No frontend file.

## Data / contracts

**Internal llm-config response** - `{"system_prompt": str, "creativity": float}`, always present.

**Internal retrieve request/response** - `POST` body `{"query": str}` (min length 1) ->
`{"context": str}`, always present (empty string for no matches).

**LLM streaming messages** - `{"type": "llm_delta", "text": str}` (one per chunk),
`{"type": "llm_complete", "text": str}` (the full accumulated reply), or
`{"type": "llm_error", "text": str}` (a fixed, caller-safe apology string).

## Testing

`apps/voice` full suite: 52 passed. `apps/api` full suite: 570 passed. `ruff check` clean on both
services. `docker compose build voice` (32s after the `.dockerignore` fix) and
`docker compose up -d voice` succeeded; `/health` returned
`{"status":"ok","active_sessions":0,"capacity":10}`.

## Notes from the build

- **`apps/voice` had no `.dockerignore`**, discovered when this step's own `docker compose build
  voice` took 405 seconds transferring a 489MB build context - `apps/voice/.venv` (508MB, holding
  `pipecat-ai`, now also `anthropic`) was being sent into the build context on every build.
  Added `apps/voice/.dockerignore` (excluding `.venv`, `__pycache__`, `.pytest_cache`,
  `.ruff_cache`, `*.egg-info`); the same build then completed in under 32 seconds.
- A pre-implementation correction in Step 1: `reset_for_next_turn()` was drafted to preserve
  `last_final_transcript` across the reset; implementing it surfaced a real bug (stale text
  reusable by the next turn's silence-detection window), so it now clears that field too, with a
  dedicated regression test.
- A mid-implementation correction in Step 5: the originally-planned "second turn arrives while the
  first LLM call is in flight" test turned out to describe a scenario the corrected design makes
  structurally unreachable (the detector stays latched until the same task's own `finally` resets
  it). Replaced with a test that proves the actually-reachable claim - a genuine second, sequential
  turn is detected and answered.
- A second real bug found while wiring Step 5: `TurnDetectionProcessor` (item 20c) had a permanent
  one-shot "already emitted" latch that would have silently blocked every turn after the first.
  Fixed to edge-trigger off the detector's own state.

## Findings

None recorded against this feature.
