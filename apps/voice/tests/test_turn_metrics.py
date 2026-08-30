import uuid
from datetime import UTC, datetime

from app.turn_metrics import TurnMetricRecord, TurnMetricsRecorder


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value


def test_turn_metric_record_has_any_leg_is_false_when_nothing_is_set() -> None:
    record = TurnMetricRecord(call_id=uuid.uuid4())

    assert record.has_any_leg() is False


def test_turn_metric_record_has_any_leg_is_true_once_a_single_leg_is_set() -> None:
    record = TurnMetricRecord(call_id=uuid.uuid4(), stt_finalized_at=datetime.now(UTC))

    assert record.has_any_leg() is True


def test_marks_are_recorded_for_the_current_generation() -> None:
    call_id = uuid.uuid4()
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    recorder = TurnMetricsRecorder(call_id=call_id, clock=clock)

    generation = recorder.current_generation()
    recorder.mark_stt_finalized(generation)
    recorder.mark_retrieval_done(generation)
    recorder.mark_llm_first_token(generation)
    recorder.mark_llm_complete(generation)
    recorder.mark_tts_first_byte(generation)
    recorder.mark_audio_out(generation)

    completed = recorder.finish_turn()

    assert completed.call_id == call_id
    assert completed.stt_finalized_at == clock.value
    assert completed.retrieval_done_at == clock.value
    assert completed.llm_first_token_at == clock.value
    assert completed.llm_complete_at == clock.value
    assert completed.tts_first_byte_at == clock.value
    assert completed.audio_out_at == clock.value


def test_a_second_mark_for_the_same_leg_is_a_no_op() -> None:
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    recorder = TurnMetricsRecorder(call_id=uuid.uuid4(), clock=clock)
    generation = recorder.current_generation()

    recorder.mark_stt_finalized(generation)
    first_value = clock.value

    clock.value = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    recorder.mark_stt_finalized(generation)

    completed = recorder.finish_turn()

    assert completed.stt_finalized_at == first_value


def test_a_mark_carrying_a_stale_generation_is_silently_ignored() -> None:
    """
    Regression guard for the exact race item 20e proved twice: a
    processor that captured the generation before a barge-in's
    finish_turn() ran must not be able to write into the *next* turn's
    fresh record just because its own mark call happens to run late.
    """

    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    recorder = TurnMetricsRecorder(call_id=uuid.uuid4(), clock=clock)
    stale_generation = recorder.current_generation()

    recorder.mark_stt_finalized(stale_generation)
    recorder.finish_turn()  # advances the generation - stale_generation is now old

    # A late-arriving mark for the turn that just finished must not touch
    # the fresh record now in progress for the next turn.
    recorder.mark_llm_first_token(stale_generation)

    completed = recorder.finish_turn()

    assert completed.llm_first_token_at is None


def test_finish_turn_advances_the_generation_and_starts_the_next_turn_clean() -> None:
    call_id = uuid.uuid4()
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    recorder = TurnMetricsRecorder(call_id=call_id, clock=clock)

    first_generation = recorder.current_generation()
    recorder.mark_stt_finalized(first_generation)
    first_completed = recorder.finish_turn()

    second_generation = recorder.current_generation()
    assert second_generation != first_generation

    clock.value = datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
    recorder.mark_stt_finalized(second_generation)
    second_completed = recorder.finish_turn()

    assert first_completed.call_id == call_id
    assert second_completed.call_id == call_id
    assert first_completed.stt_finalized_at != second_completed.stt_finalized_at
    assert second_completed.retrieval_done_at is None
