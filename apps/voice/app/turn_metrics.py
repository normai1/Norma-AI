"""
Pure per-turn latency accumulation (item 20f) - accumulates each leg's
timestamp in memory across a turn's lifecycle and hands back one completed
record when the turn concludes, so the audio path never does a database
write itself (CLAUDE.md section 6.5: "Bulk-insert transcript turns and
metrics; do not write one row per statement per turn in the audio path").
Deliberately no Pipecat or HTTP dependency here, mirroring turn_detection.py's
and sentence_chunker.py's own pure-module-plus-thin-adapter precedent -
app/media_session.py is the thin adapter that wires this into the live
pipeline, and app/turn_metrics_client.py is what actually posts a completed
record to apps/api.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from norma_shared.correlation import CallContext


@dataclass
class TurnMetricRecord:
    """
    One turn's worth of per-leg timestamps - a direct mirror of the
    persisted TurnMetric row's shape. Any leg the turn never reached (a
    failure, a barge-in, a mid-reply disconnect) simply stays None; "every
    turn writes a row" does not mean "every row is complete."

    turn_id (item 25b) is this turn's identity, minted before the turn runs
    rather than assigned by the database afterwards, because it is also what
    every log line emitted during the turn is stamped with (see
    norma_shared.correlation). A row whose identity were handed out on
    insert could not be joined to the lines that explain it.

    The token and cost fields are likewise per-turn and summed per call: a
    call's cost is the sum of its turns', and the turn is the granularity at
    which an expensive answer can actually be found.
    """

    call_id: uuid.UUID
    turn_id: uuid.UUID = field(default_factory=uuid.uuid4)
    stt_finalized_at: datetime | None = field(default=None)
    retrieval_done_at: datetime | None = field(default=None)
    llm_first_token_at: datetime | None = field(default=None)
    llm_complete_at: datetime | None = field(default=None)
    tts_first_byte_at: datetime | None = field(default=None)
    audio_out_at: datetime | None = field(default=None)
    prompt_tokens: int | None = field(default=None)
    completion_tokens: int | None = field(default=None)
    cost_micro_usd: int | None = field(default=None)

    def has_any_leg(self) -> bool:
        return any(
            getattr(self, leg) is not None
            for leg in (
                "stt_finalized_at",
                "retrieval_done_at",
                "llm_first_token_at",
                "llm_complete_at",
                "tts_first_byte_at",
                "audio_out_at",
            )
        )


class TurnMetricsRecorder:
    """
    Shared across TurnDetectionProcessor/LLMTurnProcessor/TTSProcessor for
    one session (one call_id), exactly like TurnDetector is already shared
    among them.

    Marks are guarded by a monotonically incrementing generation counter,
    not written blindly to "the current turn" - a significant,
    empirically-motivated design point. Item 20e proved, twice, that
    Pipecat gives every FrameProcessor its own per-processor frame queue,
    so two processors reacting to the *same* originating message can run
    in either order or interleave unpredictably. The same hazard applies
    here: LLMTurnProcessor's in-flight task is cancelled on barge-in via
    plain asyncio.Task.cancel(), which only takes effect at that task's
    *own* next await - so it is entirely possible for TTSProcessor's queue
    to process the barge-in first, call finish_turn(), and hand this
    recorder a fresh generation before the old, not-yet-cancelled LLM
    task's own next synchronous mark call finally runs and would otherwise
    land in the wrong turn's row.

    Each processor calls current_generation() once, at the moment it first
    reacts to a turn, and passes that captured value into every later mark
    call for that turn - never re-reading current_generation() partway
    through, which would defeat the guard entirely.
    """

    def __init__(
        self,
        call_id: uuid.UUID,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        call_context: CallContext | None = None,
    ) -> None:
        self._call_id = call_id
        self._clock = clock
        self._generation = 0
        self._call_context = call_context
        self._record = TurnMetricRecord(call_id=call_id)
        self._publish_turn_id()

    def current_generation(self) -> int:
        return self._generation

    def current_turn_id(self) -> uuid.UUID:
        return self._record.turn_id

    def _publish_turn_id(self) -> None:
        """
        Point the session's correlation context at the turn now in progress
        (item 25b), so every log line from here until the next turn carries
        this turn's identifier - the same one written to its TurnMetric row.

        The recorder is the right place for this precisely because the
        generation counter already lives here: "which turn is it" is one
        question, and having two answers to it - one for metrics, one for
        logs - is how they would drift apart.
        """

        if self._call_context is not None:
            self._call_context.turn_id = self._record.turn_id

    def mark_stt_finalized(self, generation: int) -> None:
        self._mark("stt_finalized_at", generation)

    def mark_retrieval_done(self, generation: int) -> None:
        self._mark("retrieval_done_at", generation)

    def mark_llm_first_token(self, generation: int) -> None:
        self._mark("llm_first_token_at", generation)

    def mark_llm_complete(self, generation: int) -> None:
        self._mark("llm_complete_at", generation)

    def mark_tts_first_byte(self, generation: int) -> None:
        self._mark("tts_first_byte_at", generation)

    def mark_audio_out(self, generation: int) -> None:
        self._mark("audio_out_at", generation)

    def record_token_cost(
        self,
        generation: int,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cost_micro_usd: int | None,
    ) -> None:
        """
        Attach what this turn's LLM call cost (item 25b).

        Generation-guarded like every mark, and for the same reason: a
        barge-in can advance the turn while the abandoned reply's own task
        is still unwinding, and that task must not bill its tokens to the
        turn that replaced it.

        Unlike the timestamp marks this overwrites rather than keeping the
        first value. A turn makes one LLM call whose usage arrives once, at
        the end; if a retry or a second call ever reports again, the later
        figure is the complete one.
        """

        if generation != self._generation:
            return

        self._record.prompt_tokens = prompt_tokens
        self._record.completion_tokens = completion_tokens
        self._record.cost_micro_usd = cost_micro_usd

    def _mark(self, field_name: str, generation: int) -> None:
        if generation != self._generation:
            return

        if getattr(self._record, field_name) is not None:
            return

        setattr(self._record, field_name, self._clock())

    def finish_turn(self) -> TurnMetricRecord:
        """
        Snapshot the current turn's record, advance the generation (so any
        further mark carrying the old generation is now silently ignored),
        and start the next turn clean with the same call_id.
        """

        completed = self._record
        self._generation += 1
        self._record = TurnMetricRecord(call_id=self._call_id)
        self._publish_turn_id()

        return completed
