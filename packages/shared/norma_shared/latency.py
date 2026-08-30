"""
Plane-agnostic percentile math (item 20f) - used by apps/api's TurnMetric
p95 computation and apps/voice's own latency-regression test, so it lives
here rather than being duplicated in both (unlike TTSConfig/LLMConfig's
deliberate small duplication, which carries plane-specific fetch/fail-open
logic this has none of).
"""

from collections.abc import Sequence
from math import ceil


def percentile(values: Sequence[float], p: float) -> float | None:
    """
    Nearest-rank percentile of values at fraction p (0.0-1.0). None for an
    empty sequence. A deliberate placeholder, not interpolated - the
    simplest correct definition for a first pass; revisiting it against
    real production latency distributions is item 61's job, not this
    one's.
    """

    if not values:
        return None

    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(p * len(ordered)) - 1))

    return ordered[index]
