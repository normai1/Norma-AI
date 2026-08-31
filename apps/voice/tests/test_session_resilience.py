from app.session_resilience import SessionResilienceTracker


def test_fewer_failures_than_the_threshold_never_reports_crossed() -> None:
    tracker = SessionResilienceTracker(max_consecutive_failures=3)

    assert tracker.record_turn_failed() is False
    assert tracker.record_turn_failed() is False


def test_reaching_the_threshold_reports_it() -> None:
    tracker = SessionResilienceTracker(max_consecutive_failures=2)

    assert tracker.record_turn_failed() is False
    assert tracker.record_turn_failed() is True


def test_a_single_failure_reaches_a_threshold_of_one() -> None:
    tracker = SessionResilienceTracker(max_consecutive_failures=1)

    assert tracker.record_turn_failed() is True


def test_success_resets_the_counter_so_failures_never_accumulate_across_it() -> None:
    tracker = SessionResilienceTracker(max_consecutive_failures=2)

    assert tracker.record_turn_failed() is False
    tracker.record_turn_succeeded()
    assert tracker.record_turn_failed() is False
    assert tracker.record_turn_failed() is True
