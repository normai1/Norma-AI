# Feature: PII in transcripts and logs

**From build-plan:** feature 24d (under 24, AI guardrails)
**Status:** complete

## Goal

Make it structurally hard for a caller's words, their personal details, or a
session credential to end up in an application log - and define the redaction
rules that stored transcript text will be written through once item 28 creates
somewhere to store it.

CLAUDE.md section 27 already states the rule ("Never log: passwords, API keys,
tokens, **transcript text**, caller PII"). A rule in a document is a convention;
a reviewer enforces it until the one time they don't. This feature turns it into
something the code enforces and the suite proves.

## What is actually leaking today

Found by reading the running container's logs, not assumed:

1. **Every session ticket is logged in full.** Uvicorn's access logger writes the
   WebSocket request line verbatim, and the ticket is a query parameter:

       "WebSocket /media/session?ticket=eyJhbGciOiJIUzI1NiIs...&client=v5-direct-audio" [accepted]

   That is a bearer credential granting a media session for a named assistant,
   sitting in plaintext in the log stream (CLAUDE.md section 36: never log
   secrets). Short expiry limits the window; it does not make it acceptable.

2. **The client-event log echoes raw client-controlled data** -
   `logger.info("client event: %s", data[:200])` in the WebSocket serializer.
   Today the browser only sends playback telemetry, so nothing sensitive
   arrives - but the log site trusts whatever the client sends, which is the
   shape of a leak waiting for its first new message type.

The deliberate turn-path logging is already clean: every barge-in and turn log
records word counts and assistant IDs, never utterances. Nothing in this feature
should have to change those - but nothing currently *stops* the next one being
written wrong, which is what step 4 addresses.

## Scope decision - rules now, stored transcripts with item 28

The build-plan line has two halves. The redaction half says "redaction rules for
stored transcript text". **There is no stored transcript text.** `TranscriptTurn`
arrives with item 28 (Call records and transcripts); today a transcript exists
only in the browser tab and in the voice worker's in-memory `Conversation`.

So this feature builds the **rules** - one shared, tested `redact_pii` - and puts
them to work immediately in the log path, which is where text can leak *now*.
Item 28 applies the same function at the point of write. The alternative, waiting,
would mean item 28 inventing redaction rules inline while shipping a schema.

## In scope

- `norma_shared/pii.py`: a pure `redact_pii(text)` covering emails, phone
  numbers, card-length digit runs, and long digit sequences.
- A redacting log formatter, shared by both planes, that scrubs PII **and
  credentials** from every record - message, arguments, and traceback text.
- Fixing the ticket leak so a credential never survives into the log line.
- A regression test that runs a full scripted call and asserts no caller or
  assistant utterance appears anywhere in the captured logs.

## Out of scope

- **Writing redacted transcripts to the database** - item 28 owns the table and
  the write. This feature hands it the function.
- **Per-organization redaction/retention policy** - item 29 (recording
  retention) is where a configurable policy belongs. `redact_pii` takes no
  options here; adding them later is additive.
- **Semantic/NER PII detection.** Pattern-based only. Names and addresses are
  not reliably detectable by regex, and a model in this path is not affordable
  (the same reasoning that kept an LLM judge out of 24b).
- **Recognising plain speech.** Neither the formatter nor `redact_pii` can
  tell a sentence of caller speech from any other sentence - only patterns.
  The formatter is a backstop; the rule that turn-path code logs word counts
  instead of utterances is still the actual protection, which is why step 4
  tests it directly.
- **Pipecat's TRACE frame logging.** Pipecat prints whole frames at TRACE, and
  a frame carrying a turn contains the caller's words. Rather than rewrite the
  frames the audio path depends on, `configure_logging` pins loguru so raising
  LOG_LEVEL cannot reach TRACE; getting there now takes a deliberate
  `loguru.logger.add(..., level="TRACE")`.
- **Redacting the operator's own screen.** The test-call transcript in the
  browser is the operator looking at their own call, over an authorized session -
  that is the product, not a leak.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - the redaction rules** - `packages/shared/norma_shared/pii.py`
  with `redact_pii(text)` replacing emails with `[email]`, phone numbers with
  `[phone]`, 13-19 digit card-like runs with `[card]`, and other long digit runs
  with `[number]`. Pure, no I/O, no config. *Done when:* a new test file covers
  each pattern plus the cases that must **not** be redacted (a price, a time, a
  short quantity, a year), and passes.

- [x] **Step 2 - scrub every log record** - a `RedactingFormatter` in
  `norma_shared/logging_setup.py` that applies `redact_pii` and a credential
  scrubber to the fully formatted record, so message, `%s` arguments, and
  exception tracebacks are all covered; installed on the root logger by both
  planes. *Done when:* a log call carrying an email, a card number, and a
  `ticket=<jwt>` query string emits none of them, an exception traceback
  containing an email emits none either, and both apps still log normally.

- [x] **Step 3 - close the ticket leak** - confirm against the real running
  container that a fresh test call no longer prints a usable ticket. *Done
  when:* `docker compose logs voice` for a new session shows the ticket
  redacted, and the session still connects - the fix must not disturb ticket
  validation, which reads the query parameter, not the log.

- [x] **Step 4 - prove utterances never reach the logs** - a voice-suite test
  running a full scripted turn (caller speaks, assistant replies) with logging
  captured, asserting no distinctive word from either utterance appears in the
  captured output. Bound and scrub the `client event` log while here. *Done
  when:* the test passes, and it fails if a `logger.info("%s", transcript)` is
  added anywhere in the turn path - verify that by adding one temporarily.

## Files / areas

- `packages/shared/norma_shared/pii.py` (new),
  `packages/shared/norma_shared/logging_setup.py` (new).
- `apps/voice/app/main.py` (logging setup),
  `apps/voice/app/media_session.py` (the client-event log).
- `apps/api/app/main.py` (logging setup).
- Tests: a new PII unit test, and
  `apps/voice/tests/test_transcript_never_logged.py` (new).

## Data / contracts

**Load-bearing:** `redact_pii(text) -> str` in `norma_shared.pii` is the single
redaction entry point for the whole project. Item 28 must call it when writing
`TranscriptTurn.text` rather than writing its own rules, and item 38 (post-call
delivery) must not undo it. It is pure and total: same input, same output, never
raises, never returns `None`.

Placeholders are fixed strings (`[email]`, `[phone]`, `[card]`, `[number]`) so a
redacted transcript stays readable and a reader can tell *what* was removed.

## Testing

`pytest` is declared for both backends, so the gate is live.

- Step 1 - each pattern, and the false-positive cases that must survive intact.
- Step 2 - message, arguments, and traceback paths; credential scrubbing.
- Step 3 - verified against the running container, not only in the suite.
- Step 4 - the end-to-end enforcement test, deliberately broken once to prove it
  actually catches a leak.

## Notes for the AI

- **Over-redaction is cheap in a log and expensive in a transcript.** These rules
  will later run over stored transcript text an operator relies on, so a rule
  that eats a price, an appointment time, or a house number is a defect. Test the
  negatives as carefully as the positives.
- **The formatter is a backstop, not permission.** Turn-path code still logs word
  counts and IDs, never text. Do not relax an existing log site because a scrubber
  now exists - the scrubber cannot detect a plain sentence of caller speech, only
  patterns.
- **Never regress the call.** Ticket handling is authentication (item 21a); the
  logging change must not touch how the ticket is read or validated.
- **A failing enforcement test must be provable.** Step 4 is worthless if it
  passes vacuously - break it once on purpose and say so in the step evidence.
