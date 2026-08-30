from norma_shared.latency import percentile


def test_percentile_of_empty_sequence_is_none() -> None:
    assert percentile([], 0.95) is None


def test_percentile_of_a_single_value_is_that_value() -> None:
    assert percentile([42.0], 0.95) == 42.0


def test_percentile_nearest_rank_on_a_known_dataset() -> None:
    values = [float(v) for v in range(1, 21)]  # 1.0 .. 20.0, already sorted

    assert percentile(values, 0.5) == 10.0
    assert percentile(values, 0.95) == 19.0


def test_percentile_does_not_require_pre_sorted_input() -> None:
    values = [5.0, 1.0, 4.0, 2.0, 3.0]

    assert percentile(values, 0.5) == 3.0


def test_percentile_clamps_at_the_maximum_value() -> None:
    assert percentile([1.0, 2.0, 3.0], 1.0) == 3.0
