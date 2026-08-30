# Feature: Latency instrumentation

**From build-plan:** feature 20f

**Status:** complete

## Goal

Give every conversational turn a `TurnMetric` row recording when it crossed each leg of the
pipeline (build-plan line: "per-turn TurnMetric rows across every leg, with a p95 budget enforced
in CI"), and add a regression test that fails the build if computed time-to-first-audio exceeds
CLAUDE.md's documented budget (p50 < 700ms, p95 < 1200ms). This closes CLAUDE.md section 27's
"every turn writes a TurnMetric row" requirement and section 28's "latency regression tests that
fail the build when p95 time-to-first-audio exceeds budget" - the first place this feature line
actually measures itself against its own non-negotiable numbers, rather than just asserting
functional correctness.

## Design reference

None. Backend-only.

## Architecture decisions (read before building)

- **`TurnMetric.call_id` has no foreign key yet - `Call` doesn't exist.** Build-plan item 26
  ("Call records and transcripts") owns the real `Call`/`CallLeg` tables; this feature only has a
  `/media/session` WebSocket connection, not a persisted call. `main.py` generates a fresh
  `call_id = uuid.uuid4()` per session to stand in as this session's call identity - a plain,
  unconstrained `UUID` column today, additively upgradeable to a real `ForeignKey("calls.id")`
  once item 26 lands (`ALTER TABLE ADD CONSTRAINT` is itself additive and breaks neither plane
  mid-deploy, matching CLAUDE.md section 6.2). Recorded here explicitly rather than silently
  picked, per CLAUDE.md's own instruction to reconcile a schema gap rather than guess.
- **`organization_id`/`workspace_id`/`assistant_id` are denormalized directly onto `TurnMetric`,
  not left to a future join through `Call`** - mirroring `Chunk`'s and `GlossaryEntry`'s own
  precedent of denormalizing tenant columns onto a row whose "real" owning scope is one level
  removed, specifically so tenant-scoped queries never have to wait on a later feature's join
  target. The internal endpoint resolves them from `assistant_id` server-side (identical to
  `internal/retrieval.py`'s existing `assistant_repo.get_by_id` resolution), so the voice plane
  only ever needs to send the `assistant_id` it already has.
- **No DB write happens from the audio path itself - every leg's timestamp accumulates in memory
  and one row is written per turn, at the moment the turn concludes.** CLAUDE.md section 6.5 is
  explicit: "Bulk-insert transcript turns and metrics; do not write one row per statement per
  turn in the audio path." Six separate synchronous writes per turn would add real, avoidable
  latency to the exact path this feature exists to protect. A new pure class,
  `TurnMetricsRecorder` (`apps/voice/app/turn_metrics.py`, mirroring `turn_detection.py`'s and
  `sentence_chunker.py`'s pure-module-plus-thin-adapter precedent), accumulates the six optional
  timestamps for the turn in progress; `TTSProcessor` - the same processor that already owns
  `reset_for_next_turn()` (item 20e) - calls `finish_turn()` to snapshot and clear it, then posts
  the completed record to `apps/api` as a fire-and-forget background task (`asyncio.create_task`,
  never awaited inline), matching "No blocking I/O in the audio path."
- **A significant, empirically-motivated design point: marks are guarded by a monotonically
  incrementing generation counter, not written blindly to "the current turn."** Item 20e proved,
  twice, that Pipecat gives every `FrameProcessor` its own per-processor frame queue, so two
  processors reacting to the *same* originating message can run in either order or interleave
  unpredictably (`TTSProcessor`'s own `_reply_in_progress` flag and `TurnDetectionProcessor`'s
  `recheck()` both exist because of this). The same hazard applies here: `LLMTurnProcessor`'s
  in-flight task is cancelled on barge-in via plain `asyncio.Task.cancel()`, which only takes
  effect at that task's *own* next `await` - so it is entirely possible for `TTSProcessor`'s
  queue to process the barge-in first, call `finish_turn()`, and hand the (now-cleared) recorder
  a fresh generation *before* the old, not-yet-cancelled LLM task's own next synchronous mark call
  finally runs and would otherwise land in the wrong turn's row. `TurnMetricsRecorder.
  current_generation()` is captured once, at the moment each processor first reacts to a turn
  (`TurnDetectionProcessor` at its `turn_ended` edge; `LLMTurnProcessor` and `TTSProcessor` when
  each first sees that same `turn_ended` message), and passed explicitly into every later mark
  call for that turn; `finish_turn()` advances the generation, and any mark call carrying a
  stale generation is silently ignored. This is more machinery than a single counter would
  normally warrant, but it is the smallest fix that actually closes a proven, not hypothetical,
  race in this exact pipeline.
- **`tts_first_byte_at` and `audio_out_at` are captured as two separate, back-to-back marks
  around the same `push_frame` call, not merged into one.** In this implementation they will
  differ by microseconds at most, since Pipecat's own frame push is an in-process, non-blocking
  hand-off, not a real socket write - CLAUDE.md's own signal list keeps them conceptually
  distinct (the moment synthesis produced audio vs. the moment it left the pipeline), and a real
  transport's own send latency is exactly what item 61's real load/latency validation is for.
  Recorded honestly rather than collapsed, matching this feature line's established pattern of
  documenting rather than hiding a today-narrow distinction.
- **Only the *first* sentence's first audio chunk of a turn is marked** - `TTSProcessor` already
  plays sentences sequentially (item 20e); `tts_first_byte_at`/`audio_out_at` answer "time to
  first audio," not "time to every sentence," so a per-turn guard (compared against the captured
  generation) prevents the second and later sentences' own first bytes from overwriting it.
- **A real gap found while red-teaming this spec, before any code existed: `_reset_turn()` is the
  *only* place a turn's accumulated record gets flushed and posted - but a caller hanging up
  mid-reply (`EndFrame`/`CancelFrame`, which `TTSProcessor` already reacts to by cancelling its
  player task) never reaches it.** Without a fix, the single most realistic way a turn ends
  abnormally - the call simply drops - would silently lose that turn's entire timing record,
  directly contradicting "every turn writes a row." `TTSProcessor`'s existing `EndFrame`/
  `CancelFrame` handling also calls `turn_metrics.finish_turn()` and fires the same
  fire-and-forget post, guarded to skip posting if the flushed record has *no* legs set at all
  (a session that disconnects with no turn ever having started has nothing worth recording).
- **A second real gap, found while testing that same disconnect path, not by inspection:
  `self._player_task.cancel()` did not actually stop `_play_sentences()`'s loop.** Its own
  `except asyncio.CancelledError: pass` was written for a *different* cancellation - barge-in
  cancelling only `_current_playback` (the child), which correctly falls through to
  `_maybe_reset_after_reply()` and keeps the loop running for the next sentence. But cancelling
  the *player task itself* (via `EndFrame`/`CancelFrame`) also cancels whatever child it is
  currently awaiting, landing on the exact same `except` line - so the loop caught it, shrugged,
  and kept running: one more spurious `_maybe_reset_after_reply()` call on a connection that was
  already closing, which (since `_llm_finished`/queue/playback state still satisfied the reset
  condition) posted a second, entirely empty TurnMetric record right after the real one. Fixed by
  checking `asyncio.current_task().cancelling()` (Python 3.11+): nonzero only when this task's
  *own* cancellation was requested, not just its child's - re-raising in that case actually stops
  the loop, exactly once, instead of silently absorbing it.
- **The p95 percentile function is genuinely plane-agnostic pure math, so it lives in
  `packages/shared`, not duplicated** - `apps/api`'s `compute_time_to_first_audio_p95` and
  `apps/voice`'s own latency-regression test both call the same `norma_shared.latency.percentile`
  rather than each rolling their own, unlike `TTSConfig`/`LLMConfig`'s deliberate small
  duplication (those carry plane-specific fetch/fail-open logic; this is zero-dependency math with
  nothing plane-specific to duplicate).
- **The percentile method is nearest-rank, not interpolated** - simplest correct definition for a
  first pass, consistent with this feature line's other named placeholders (`is_semantically_
  complete`, `SentenceChunker`'s punctuation-only split); revisiting the exact percentile method
  against real production latency distributions is squarely item 61's job, not this one's.
- **"p95 budget enforced in CI" means "enforced by `pytest`," today's actual Verify gate
  (`AGENTS.md`), not a claim about a not-yet-built GitHub Actions pipeline.** Build-plan item 59
  ("Initial CI/CD pipeline") and item 22 ("Voice pipeline test harness," the real fixture-audio
  replay tool) are both still unbuilt. This feature's own latency-regression test runs several
  turns through the real pipeline with mock providers given small, deliberately-chosen realistic
  delays and asserts computed p95/p50 stay under budget - proving the instrumentation and the
  computation are correct and would trip on a real regression, not proving real production
  latency is within budget (that claim is item 61's, against real production topology). Once
  item 59 stands up real CI, it runs this exact `pytest` suite; nothing here needs to change.

## In scope

- **`packages/shared/norma_shared/latency.py`** - pure logic: `percentile(values: Sequence[float],
  p: float) -> float | None` (nearest-rank; `None` for an empty sequence).
- **`apps/voice/app/turn_metrics.py`** - pure logic: `TurnMetricRecord` (dataclass: `call_id` plus
  the six optional `datetime` legs) and `TurnMetricsRecorder` (generation-guarded marks; see
  Architecture decisions).
- **`apps/api/app/models/turn_metric.py`** - `TurnMetric`: `organization_id`/`workspace_id`/
  `assistant_id` (indexed, FK'd, matching `GlossaryEntry`'s convention), `call_id` (indexed, no FK
  - see Architecture decisions), the six nullable `timestamptz` legs, plus the standard UUID
  primary key and `created_at`/`updated_at` (indexed, for time-range queries).
- **New Alembic migration** creating `turn_metrics`.
- **`apps/api/app/repositories/turn_metric.py`** - `create(db, *, organization_id, workspace_id,
  assistant_id, call_id, **six optional legs) -> TurnMetric`; `list_since(db, since: datetime) ->
  list[TurnMetric]`.
- **`apps/api/app/services/turn_metrics.py`** - `record_turn_metric(db, *, assistant_id, call_id,
  **six optional legs) -> TurnMetric` (resolves `assistant_id` to its org/workspace via
  `assistant_repo.get_by_id`, raising `AssistantNotFound` if missing, mirroring
  `internal/retrieval.py`'s exact resolution); `compute_time_to_first_audio_p95(rows:
  Sequence[TurnMetric]) -> float | None` (milliseconds, using only rows where both
  `stt_finalized_at` and `audio_out_at` are present; `None` if none qualify).
- **`apps/api/app/api/internal/turn_metrics.py`** - `POST
  /internal/v1/assistants/{assistant_id}/turn-metrics`, body: `call_id` plus the six optional
  ISO-datetime legs; same `RequireInternalSecret` auth as every existing internal endpoint;
  returns `{"id": str}`. Registered in `apps/api/app/main.py`.
- **`apps/voice/app/turn_metrics_client.py`** - `record_turn_metric(assistant_id, record:
  TurnMetricRecord, *, client=None) -> None`: fire-and-forget POST to the endpoint above; catches
  `httpx.HTTPError` and returns silently (a lost metric must never affect the call, mirroring
  every existing `apps/voice` client's fail-open precedent, though this one has nothing useful to
  fail open *to* - it just never raises).
- **`apps/voice/app/media_session.py`**:
  - `TurnDetectionProcessor` marks `stt_finalized_at` at its existing `turn_ended` edge-trigger.
  - `LLMTurnProcessor` captures the current generation when it reacts to `turn_ended`, passes it
    into `_run_llm_turn`, and marks `retrieval_done_at` (after `fetch_retrieved_context` returns),
    `llm_first_token_at` (on the first delta only), and `llm_complete_at` (on successful stream
    completion only, not on `llm_error`).
  - `TTSProcessor` captures the current generation the same way, marks `tts_first_byte_at`/
    `audio_out_at` on the first sentence's first audio chunk only, and calls
    `turn_metrics.finish_turn()` plus the fire-and-forget POST inside `_reset_turn()` - before
    `recheck()`, so a barge-in's second turn is never handed a stale generation (see Architecture
    decisions). Also flushes and posts on `EndFrame`/`CancelFrame` (skipping the post if nothing
    was ever marked), so a mid-reply disconnect still records whatever legs that turn reached.
  - Pipeline builder constructs one shared `TurnMetricsRecorder` per session and threads it (and
    `assistant_id`, where a processor doesn't already have it) into the three processors above.
- **`apps/voice/app/main.py`** - generates `call_id = uuid.uuid4()` per session, passes it through.
- **A new end-to-end latency-regression test** (`apps/voice/tests/test_latency_regression.py`)
  proving computed p50/p95 time-to-first-audio, under controlled mock-provider timing, stays
  within CLAUDE.md's documented budget.

## Out of scope

- **The real `Call`/`CallLeg` tables and a real foreign key from `TurnMetric.call_id`.** Item 26
  by name; this feature's `call_id` is a session-scoped placeholder (see Architecture decisions).
- **Any user-facing read surface for `TurnMetric`** (call detail's latency disclosure, item 28;
  call analytics' latency percentiles, item 48). This feature only writes rows and computes a
  percentile for its own regression test; nothing here is exposed to an operator yet.
- **A real fixture-audio conversation replay harness.** Item 22 by name; this feature's own
  regression test drives the real pipeline directly (the same technique `test_media_session.py`
  already uses), not a dedicated replay tool.
- **A real GitHub Actions CI pipeline.** Item 59 by name; see Architecture decisions for what
  "enforced in CI" means today.
- **Validating real production latency, or tuning the percentile method against real
  distributions.** Item 61 by name.
- **Retention, partitioning, or archival of `turn_metrics` rows.** CLAUDE.md section 6.5 flags
  this as a future concern for every high-volume table; nothing here makes it harder later (a
  plain append-only table with no cross-references into it yet), but building it now would be
  speculative.
- **Instrumenting STT's own partial/interim-transcript timing, or per-tool-call latency
  (`ToolInvocation.latency_ms`).** Neither is one of `TurnMetric`'s six locked columns; both are
  separate, already-provisioned-for concerns.

## Build steps

- [x] **Step 1 - pure logic: percentile helper and the turn-metrics recorder**
  - `packages/shared/norma_shared/latency.py` (new): `percentile`.
  - `apps/voice/app/turn_metrics.py` (new): `TurnMetricRecord`, `TurnMetricsRecorder`.
  *Done when:* `apps/api/tests/test_latency.py` proves `percentile` against a known small
  dataset (including the empty-sequence `None` case and an exact-boundary case). `apps/voice/
  tests/test_turn_metrics.py` proves: each `mark_*` sets its field exactly once (a second call
  with the same generation is a no-op); a mark carrying a stale (already-superseded) generation
  is silently ignored; `finish_turn()` returns the accumulated record, advances the generation,
  and starts the next turn clean with the same `call_id`. Full `apps/api` and `apps/voice` suites
  green. `ruff check` clean on both.

- [x] **Step 2 - `TurnMetric` model/migration/service, internal endpoint, and the `apps/voice`
  client**
  - `apps/api/app/models/turn_metric.py`, matching migration.
  - `apps/api/app/repositories/turn_metric.py`, `app/services/turn_metrics.py`.
  - `apps/api/app/api/internal/turn_metrics.py`, registered in `main.py`.
  - `apps/voice/app/turn_metrics_client.py`.
  *Done when:* `apps/api/tests/test_turn_metrics.py` proves `record_turn_metric` persists a row
  with the right organization/workspace/assistant resolved and every leg (including a mix of
  present and `None` legs, proving a partial/interrupted turn is representable) stored correctly,
  and raises `AssistantNotFound` for an unknown assistant; `compute_time_to_first_audio_p95`
  returns the correct value for a known set of rows and `None` when no row has both anchor
  timestamps. `apps/api/tests/test_internal_turn_metrics.py` proves 200 (with a real row
  persisted), 404 for an unknown assistant, and 401 for a missing/wrong secret. `apps/voice/
  tests/test_turn_metrics_client.py` proves the client posts the right payload on success and
  silently swallows a non-200 response and a connection failure (never raises). Full `apps/api`
  and `apps/voice` suites green. `ruff check` clean on both.

- [x] **Step 3 - wire per-leg marks into the real pipeline**
  - `apps/voice/app/media_session.py`: the three processors' marks, the shared
    `TurnMetricsRecorder`, and `_reset_turn()`'s finish-and-post (see Architecture decisions for
    exact ordering).
  - `apps/voice/app/main.py`: generate and thread `call_id`.
  *Done when:* the existing `test_media_session.py` tests are extended (monkeypatching
  `turn_metrics_client.record_turn_metric` to capture, not send, the payload - mirroring how
  every other internal-API call is already mocked in this file) to prove: a normal single-
  sentence turn's captured record has all six legs set, in non-decreasing order except
  `llm_complete_at` (which is allowed to land after `tts_first_byte_at`/`audio_out_at`, since
  TTS starts speaking before the LLM finishes - see Architecture decisions); a TTS-failure
  turn's record has every LLM leg set but `audio_out_at` still `None`; a barge-in scenario
  posts *two* records - the interrupted first turn's (partial: no `audio_out_at`) and the
  second turn's own (complete), never a single merged or corrupted record; a connection that
  closes mid-reply (before `reset_for_next_turn` ever runs) still posts one partial record via
  the `EndFrame`/`CancelFrame` path, and a connection that closes with no turn ever started posts
  nothing. Full `apps/voice` suite green. `ruff check apps/voice` clean.

- [x] **Step 4 - latency-regression test**
  - `apps/voice/tests/test_latency_regression.py` (new).
  *Done when:* the test drives at least 20 turns through the real WebSocket pipeline (mock STT/
  LLM/TTS/scripted VAD, matching every other end-to-end test in this feature line) with small,
  deliberately-chosen realistic per-leg delays, captures each turn's posted record via the same
  monkeypatched client, computes p50 and p95 of `audio_out_at - stt_finalized_at` in milliseconds
  using `norma_shared.latency.percentile`, and asserts both stay under CLAUDE.md's documented
  budget (p50 < 700ms, p95 < 1200ms). Full `apps/voice` suite green. `ruff check apps/voice`
  clean. `docker compose build voice && docker compose up -d voice` succeeds; `/health` still 200.

## Files / areas

**New**
- `packages/shared/norma_shared/latency.py`
- `apps/voice/app/turn_metrics.py`, `app/turn_metrics_client.py`
- `apps/voice/tests/test_turn_metrics.py`, `test_turn_metrics_client.py`, `test_latency_regression.py`
- `apps/voice/tests/conftest.py` (not in the original plan - extracted while building Step 4, so
  the new latency-regression test could reuse `test_media_session.py`'s existing session-setup
  helpers without an ad hoc cross-test-file import; mirrors `apps/api/tests/conftest.py`'s own
  precedent for helpers shared across multiple test files)
- `apps/api/app/models/turn_metric.py`, a new Alembic migration
- `apps/api/app/repositories/turn_metric.py`, `app/services/turn_metrics.py`
- `apps/api/app/api/internal/turn_metrics.py`
- `apps/api/tests/test_latency.py`, `test_turn_metrics.py`, `test_internal_turn_metrics.py`

**Modified**
- `apps/voice/app/media_session.py` (the three processors' marks, shared recorder wiring,
  `_reset_turn()`'s finish-and-post, the `_play_sentences()` cancellation fix - see Architecture
  decisions)
- `apps/voice/app/main.py` (generate and thread `call_id`)
- `apps/voice/tests/test_media_session.py` (session-setup helpers moved to the new `conftest.py`;
  mocks the turn-metrics client; extends existing tests; adds new turn-metrics-specific tests)
- `apps/api/app/main.py` (new internal router registration)
- `apps/api/app/db/base.py` (registers `TurnMetric` for Alembic autogenerate)

**Unchanged**
- `apps/api/app/api/internal/glossary.py`, `internal/llm_config.py`, `internal/retrieval.py`,
  `internal/turn_detection.py`, `internal/tts_config.py`, `internal_deps.py` - reused as-is.
- No frontend file.

## Data / contracts

**`TurnMetric` row** - `organization_id`, `workspace_id`, `assistant_id` (UUID, FK'd, indexed),
`call_id` (UUID, indexed, no FK - see Architecture decisions), `stt_finalized_at`,
`retrieval_done_at`, `llm_first_token_at`, `llm_complete_at`, `tts_first_byte_at`, `audio_out_at`
(all nullable `timestamptz`), plus the standard `id`/`created_at`/`updated_at`. This is the exact
shape `project-overview.md` already locks for this table.

**Internal turn-metrics request** - `POST /internal/v1/assistants/{assistant_id}/turn-metrics`:
`{"call_id": str, "stt_finalized_at": str | null, "retrieval_done_at": str | null,
"llm_first_token_at": str | null, "llm_complete_at": str | null, "tts_first_byte_at": str | null,
"audio_out_at": str | null}` (ISO-8601 datetimes). Response: `{"id": str}`.

**`TurnMetricRecord`** (`apps/voice/app/turn_metrics.py`) - `call_id: uuid.UUID` plus the six
`datetime | None` legs, mirroring the persisted row's shape exactly (the wire payload above is
a direct serialization of this).

## Testing

The backend gate is live for both services, matching this feature line's own established shape:
pure logic (Step 1) gets full unit coverage; the API model/service/endpoint and voice-side client
(Step 2) mirror `tts_config`'s own test shape exactly; pipeline wiring (Step 3) extends the
established `test_media_session.py` file; the full pipeline's own latency claim (Step 4) gets a
dedicated end-to-end regression test - no real network, no real model, no real voice, anywhere in
the suite.

## Notes for the AI

- **Order matters inside `_reset_turn()`: call `turn_metrics.finish_turn()` (and fire the POST)
  before `recheck()`, not after.** `recheck()` is what can cause `TurnDetectionProcessor` to mark
  `stt_finalized_at` for an already-arrived second turn (item 20e's barge-in precedent); if
  `finish_turn()` ran second, that mark would land in the wrong (about-to-be-cleared) record.
- **Capture the generation once, at task/reaction start, and pass it explicitly - never call
  `current_generation()` again later inside the same turn's own work.** Re-reading it right
  before a later mark would defeat the whole guard, since the value could have already advanced
  by then.
- **`llm_complete_at` is only marked on a successful stream completion, never on `llm_error`** -
  mirroring item 20e's own `_handle_llm_finished(is_error=...)` distinction; an errored turn's row
  legitimately has some `None` legs, which is exactly what "every turn writes a row" is supposed
  to tolerate, not paper over.
- **A push, if any, at the end of this feature does not need your explicit go-ahead** - the user's
  `/feature` invocation for this item included the standing "don't ask for any permission, go
  with your recommendation" override, matching items 20a, 20b, 20d, and 20e.
