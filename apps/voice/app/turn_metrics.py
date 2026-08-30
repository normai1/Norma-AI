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


@dataclass
class TurnMetricRecord:
    """
    One turn's worth of per-leg timestamps - a direct mirror of the
    persisted TurnMetric row's shape. Any leg the turn never reached (a
    failure, a barge-in, a mid-reply disconnect) simply stays None; "every
    turn writes a row" does not mean "every row is complete."
    """

    call_id: uuid.UUID
    stt_finalized_at: datetime | None = field(default=None)
    retrieval_done_at: datetime | None = field(default=None)
    llm_first_token_at: datetime | None = field(default=None)
    llm_complete_at: datetime | None = field(default=None)
    tts_first_byte_at: datetime | None = field(default=None)
    audio_out_at: datetime | None = field(default=None)

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
    ) -> None:
        self._call_id = call_id
        self._clock = clock
        self._generation = 0
        self._record = TurnMetricRecord(call_id=call_id)

    def current_generation(self) -> int:
        return self._generation

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

        return completed
