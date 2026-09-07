# Feature: Blocked topics

**From build-plan:** feature 24c (under 24, AI guardrails)
**Status:** complete

## Goal

Let an operator name subjects their assistant must not discuss - legal advice,
medical advice, competitor pricing, whatever their business cannot afford to
have answered by a machine - and enforce that outside the model, so no prompt
wording or caller persistence can talk its way past it.

## Scope decision - topics now, actions with item 35

The build-plan line reads "topic and action allow-lists". The action half is
**deferred to item 35** (Tool permission framework: declarative per-assistant
skill enablement, enforced independently of model output).

There are no tools or skills in the codebase - nothing to allow or refuse - so
an action allow-list built here would have nothing to enforce, and item 35 would
then either duplicate it or replace it. One enforcement point for actions, built
where the actions are.

## In scope

- `Assistant.blocked_topics`: operator-supplied phrases, per assistant.
- Migration, update/response schemas, and the editor field to manage them.
- The list reaching the voice worker with the rest of an assistant's LLM config.
- Enforcement **on the caller's words, before the model is called at all** - so
  the model never sees a blocked request and cannot be argued into answering it.
- Tests on both planes.

## Out of scope

- **Action/skill allow-lists** - item 35, per the decision above.
- **Semantic topic matching.** A blocked topic matches on the operator's own
  phrasing, not on meaning: "legal advice" will not catch "can I sue them".
  Operators list the phrasings they care about. An LLM-based intent classifier
  in the turn path would blow the latency budget - the same reason 24b rejected
  an LLM-as-judge.
- Blocking the assistant's *output* by topic - 24b already validates output.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - store the list** - `blocked_topics` on `Assistant` (JSONB
  defaulting to an empty list, matching how `workspace.settings` and
  `chunk.metadata` already store structured values), a migration, and the field
  on `AssistantUpdate`/`AssistantResponse` with entries trimmed and blanks
  dropped. *Done when:* `alembic upgrade head` runs clean, a PATCH round-trips
  the list, and `pytest apps/api/tests/test_assistants.py` passes.

- [x] **Step 2 - deliver it to the voice worker** - add `blocked_topics` to the
  internal `llm-config` response and to `LLMConfig` on both sides, defaulting
  to empty when the fetch fails open. *Done when:* the internal endpoint
  returns the list, and `test_internal_llm_config.py` plus
  `test_llm_config_client.py` pass.

- [x] **Step 3 - enforce before the model** - a pure
  `blocked_topic_in(text, topics)` in `guardrails.py`, and `LLMTurnProcessor`
  checking the caller's transcript with it: on a match, speak a refusal and
  skip the LLM call entirely. *Done when:* a blocked turn produces the refusal
  with `MockLLM.call_count == 0`, an unrelated turn answers normally, and an
  assistant with no blocked topics behaves exactly as before.

- [x] **Step 4 - the editor field** - blocked topics in the assistant editor's
  Technical tab, one per line, saving through the existing PATCH. *Done when:*
  `npm run lint` and `npx tsc --noEmit` pass and the field round-trips.

## Files / areas

- `apps/api/app/models/assistant.py`, a new `alembic/versions/` migration.
- `apps/api/app/schemas/assistant.py`, `app/services/llm_config.py`, and the
  route serving `llm-config`.
- `apps/voice/app/llm_config_client.py`, `app/guardrails.py`,
  `app/media_session.py`.
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx`.
- Tests: `test_assistants.py`, `test_internal_llm_config.py`,
  `test_llm_config_client.py`, `test_guardrails.py`, and a new
  `apps/voice/tests/test_blocked_topics.py`.

## Data / contracts

**Load-bearing:** `blocked_topics` is a JSONB list of strings on `assistants`,
defaulting to `[]`, and appears in the `llm-config` internal response. Item 35
adds enabled skills alongside it rather than reworking it.

The migration must be additive with a server default, since the API and the
voice worker deploy separately and will briefly run different code against the
same schema (CLAUDE.md section 6.2).

The refusal reuses 24b's `SAFE_FALLBACK` shape, so a caller never meets two
different refusal voices.

## Testing

`pytest` is declared for both backends, so the gate is live; the frontend step
rides on lint plus type-check per `coding-standards.md`.

- Step 1 - schema round-trip and trimming.
- Step 2 - both config paths, including the fail-open default.
- Step 3 - blocked, unblocked, and empty-list cases, asserting the LLM was
  never called on a block.
- Step 4 - lint and type-check.

## Notes for the AI

- **Enforcement happens on input, before the LLM.** That is what makes it
  independent of model output (CLAUDE.md section 36: model output never
  authorizes anything). Do not implement this as a prompt instruction and call
  it enforced - the prompt may also mention it, but the check is the block.
- **Match conservatively, case-insensitively, on whole phrases.** Refusing an
  innocent question through a stray substring match is the failure mode here -
  the mirror of 24b's false positives.
- **Never log the caller's words** (CLAUDE.md section 27): log which topic
  matched, not the transcript.
- **Operator configuration is authoritative** - an empty list means no blocking
  at all, and must never quietly acquire defaults.
