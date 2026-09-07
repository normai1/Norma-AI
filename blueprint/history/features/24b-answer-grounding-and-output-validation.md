# Feature: Answer grounding and output validation

**From build-plan:** feature 24b (under 24, AI guardrails)
**Status:** complete

## Goal

Stop the assistant stating business facts it was never given, and stop it
claiming to have done things it cannot do. A caller quoted an invented price, or
told their appointment is booked when nothing was booked, has been actively
misled - worse than being told "I don't have that detail."

24a contained untrusted text so it cannot instruct the model. This is the other
half: checking what the model is about to say before the caller hears it.

## Settled design decision - where validation runs

Validation runs in `LLMTurnProcessor`, per sentence, before each `llm_delta` is
pushed downstream.

The alternative was `TTSProcessor`, where sentences are already chunked - but it
has no access to the turn's retrieved knowledge, and shipping that knowledge
downstream would send it over the WebSocket to the browser, leaking it to the
client and bloating every turn.

Validating in `LLMTurnProcessor` looked like it would cost streaming latency,
and it does not: **`TTSProcessor` is already sentence-gated.** It buffers deltas
through `SentenceChunker` and synthesizes nothing until a complete sentence
exists, so gating deltas at sentence boundaries adds no audible delay. The
caller hears the same timing; only the on-screen transcript changes, from
token-by-token to sentence-by-sentence.

## In scope

- A pure validator: does this sentence assert a specific business fact the
  turn's grounded text does not contain, or claim a completed action?
- Grounded-answer rules in the system prompt, referring to 24a's block.
- Per-sentence validation in `LLMTurnProcessor`, replacing a violating reply
  with a safe fallback and abandoning the rest of that reply.
- Tests in both directions: violations caught, ordinary answers untouched.

## Out of scope

- **Topic and action allow-lists** (24c) and **PII** (24d).
- **Any second model call to judge grounding.** This runs in the per-turn path;
  an LLM-as-judge would blow the latency budget and the per-turn cost.
  Deterministic local checks only.
- **Semantic grounding.** "Nine to five" will not be recognised as the same
  fact as "09:00-17:00". This catches specific asserted values with no support
  at all, which is the case that actually misleads a caller.
- Tool-call verification (items 35+) - no tool can run yet.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - the validator** - extend `apps/voice/app/guardrails.py` with
  `find_unsupported_claim(sentence, *, grounded_text) -> str | None`, returning
  a short reason or `None`, plus `SAFE_FALLBACK`. Flags two things,
  conservatively: **(a) asserted values** - money amounts and clock
  times/opening hours - whose normalised form does not appear in
  `grounded_text`; **(b) completed-action claims** - "I've booked", "I have
  sent", "that's confirmed", "I've cancelled" - which are *always* unsupported,
  because no tool exists to have done them (items 35+).
  *Done when:* `pytest tests/test_guardrails.py` covers a quoted price absent
  from knowledge (flagged), the same price present in knowledge (allowed), a
  completion claim (flagged), "I'll have someone call you back" (allowed), and
  an ordinary answer with no numbers (allowed). Also the commonest real case:
  **empty `grounded_text`** - retrieval returned nothing, so any quoted price
  or opening time is by definition unsupported and must be flagged.

- [x] **Step 2 - grounded-answer rules in the prompt** - extend 24a's standing
  rule: answer business specifics only from the reference block, and when it
  isn't there, say so and offer a callback rather than estimating. Prevention
  first; Step 3 is the enforcement behind it. *Done when:* a test asserts the
  rules are present for all three prompt resolutions, and the operator's own
  prompt still appears verbatim ahead of them.

- [x] **Step 3 - enforce before speaking** - `LLMTurnProcessor` buffers deltas
  through `SentenceChunker`, validates each completed sentence, and pushes it on
  only if it passes. On a violation: push `SAFE_FALLBACK` as the reply, stop
  consuming the LLM stream, and log the reason with no reply text.

  If the validator itself raises, **fail open** - speak the sentence and log the
  error. A guardrail bug must not be able to mute an otherwise working
  assistant; CLAUDE.md's "silence is the worst possible failure" outranks
  catching one more invented price. *Done when:* the trailing partial sentence
  at `llm_complete` is validated and spoken rather than dropped; a reply with
  no sentence-ending punctuation at all is still validated once at
  `llm_complete`; a violating turn speaks only the fallback; a validator that
  raises still speaks; and the full `apps/voice` suite passes.

- [x] **Step 4 - pipeline tests** - `apps/voice/tests/test_grounding.py`,
  through the real pipeline, asserting what a caller would actually get: an
  ungrounded price replaced by the fallback with the rest of the reply
  abandoned; the same price present in knowledge spoken as written; a
  completion claim replaced; and a normal multi-sentence answer spoken
  unchanged. *Done when:* all four assert on `llm_complete` and the spoken
  text, not on internal state.

## Files / areas

- `apps/voice/app/guardrails.py` - validator and fallback text.
- `apps/voice/app/conversation.py` - grounded-answer rules.
- `apps/voice/app/media_session.py` - `LLMTurnProcessor`'s delta loop.
- `apps/voice/tests/test_guardrails.py` - extended.
- `apps/voice/tests/test_grounding.py` - new pipeline tests.

## Data / contracts

No schema change.

**Load-bearing:** `SAFE_FALLBACK` is what a caller hears whenever the assistant
declines, so 24c's blocked-topic path reuses it rather than inventing a second
phrasing. `find_unsupported_claim`'s signature is the seam 24c hooks its action
checks into.

Depends on 24a's contract: grounded text is what sits inside the `KNOWLEDGE`
block, so the validator is handed the raw retrieved context, not the assembled
prompt.

## Testing

`pytest` is the declared backend test command, so the gate is live and every
step here is logic-bearing.

- Step 1 - the validator's five cases, both directions.
- Step 2 - prompt-content assertions across the three resolutions.
- Step 3 - the existing suite as the regression check.
- Step 4 - the four end-to-end cases.

Run with the provider overrides this environment needs:
`docker compose exec -T -e STT_PROVIDER=mock -e TTS_PROVIDER=mock -e LLM_PROVIDER=mock -e GROQ_API_KEY= -e ELEVENLABS_API_KEY= voice python -m pytest -q`

## Notes for the AI

- **False positives are the real risk here.** Replacing a correct answer with
  "I don't have that" makes the assistant useless, and it fails silently from
  the operator's point of view - nobody sees it happen. Keep the checks narrow
  and literal; when in doubt, let the sentence through. Every check ships a
  passing "this is fine" test beside its "this is caught" test.
- **Fail open, always.** Both on an unexpected validator error and on any
  input the checks cannot parse. The failure this feature could introduce -
  an assistant that answers "I don't have that" to everything - is worse than
  the failure it removes, and far harder to notice.
- **Per-turn audio path.** Local string work only - no model call, no I/O.
- **Never log reply or transcript text** (CLAUDE.md section 27). Log the reason
  code and the turn, never what was said.
- **Operator configuration stays authoritative.** These rules constrain the
  model; they never replace or truncate operator wording.
- The trailing fragment at `llm_complete` is a real sentence the caller should
  hear (see `TTSProcessor._handle_llm_finished`) - validate it, don't drop it.
- 24a's `contain_untrusted` and the `KNOWLEDGE` block are already in place;
  this feature adds to that module rather than replacing anything in it.
