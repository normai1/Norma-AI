"""
The whole document, not the first few pages.

The original complaint: a 50-page PDF produced 8 FAQs because generation
read its opening 12,000 characters and never looked at the other 92%. The
windowing that fixed it then had to survive being resized - windows are now
sized to divide the provider's per-minute budget, which made them smaller,
which would have run a long document into the window ceiling instead.
"""

import pytest

from app.services.faq_generation import (
    _MIN_SOURCE_TEXT_CHARS,
    MAX_SOURCE_TEXT_CHARS,
    MAX_SOURCE_WINDOWS,
    _per_call_overhead_tokens,
    _windows,
    window_chars_for_budget,
)
from app.services.token_rate_limiter import estimate_tokens

# 50 pages at roughly 280 words of ordinary prose.
_FIFTY_PAGES = " ".join(f"sentence{i} about the business" for i in range(5_200))


@pytest.mark.parametrize("budget", [3_000, 4_000, 6_000, 8_000, 39_000, 200_000])
def test_a_fifty_page_document_is_covered_end_to_end(budget: int) -> None:
    """
    Whatever the budget, and therefore whatever the window size, the last
    words of the document must reach a window.
    """

    chars = window_chars_for_budget(budget)
    windows = _windows(_FIFTY_PAGES, max_chars=chars)

    assert len(windows) <= MAX_SOURCE_WINDOWS
    # The document's own ending, not just its length, is what proves nothing
    # was quietly dropped off the back.
    assert _FIFTY_PAGES.strip().endswith(windows[-1][-40:])


@pytest.mark.parametrize("budget", [3_000, 4_000, 6_000, 8_000, 39_000, 200_000])
def test_the_window_size_stays_inside_its_bounds(budget: int) -> None:
    chars = window_chars_for_budget(budget)

    assert _MIN_SOURCE_TEXT_CHARS <= chars <= MAX_SOURCE_TEXT_CHARS


@pytest.mark.parametrize("budget", [4_000, 5_000, 6_000, 8_000, 39_000, 200_000])
def test_at_least_one_window_fits_inside_a_minute(budget: int) -> None:
    """
    A window that cannot fit its own budget would wait for room that never
    comes - and the cost it has to fit inside includes everything the call
    carries before the document does.
    """

    chars = window_chars_for_budget(budget)
    cost = estimate_tokens("x" * chars) + _per_call_overhead_tokens()

    assert cost <= budget


def test_the_window_is_as_large_as_the_minute_allows() -> None:
    """
    Total cost is the document's own text plus a fixed per-call overhead
    once per window, so fewer and larger windows cost less - and the scarce
    thing is the daily allowance, not the minute.

    An earlier version sized windows to pack two into a minute while
    ignoring that overhead, so the windows it chose cost 5,115 tokens
    against the 4,000 they were sized for: one per minute after all, having
    paid the overhead three extra times. Measured on the user's own 50-page
    PDF as 17 windows where 14 would do.
    """

    budget = 8_000
    chars = window_chars_for_budget(budget)
    cost = estimate_tokens("x" * chars) + _per_call_overhead_tokens()

    assert cost <= budget
    # Nothing bigger would have fitted.
    bigger = min(MAX_SOURCE_TEXT_CHARS, chars + 1_000)
    assert (
        bigger == chars
        or estimate_tokens("x" * bigger) + _per_call_overhead_tokens() > budget
    )


def test_the_overhead_a_window_is_sized_against_is_the_worst_case() -> None:
    """
    The avoid-list grows as a document is worked through. Sizing against an
    empty one would put a window over budget exactly when a long document is
    halfway done - which is when it can least afford to be refused.
    """

    from app.services.faq_generation import _RECENT_QUESTIONS_SHOWN, _avoid_clause

    realistic = [
        "What are the subscription pricing options for recruiters?",
        "How quickly will I receive interview recordings and reports?",
    ] * (_RECENT_QUESTIONS_SHOWN // 2)

    assert estimate_tokens(_avoid_clause(realistic)) <= _per_call_overhead_tokens()


def test_every_character_of_the_document_reaches_some_window() -> None:
    """
    Windows split on paragraph and line boundaries, so they are not a
    straight slicing - but nothing may fall between two of them.
    """

    windows = _windows(_FIFTY_PAGES, max_chars=window_chars_for_budget(8_000))
    rejoined = " ".join(windows)

    for probe in (0, len(_FIFTY_PAGES) // 2, len(_FIFTY_PAGES) - 60):
        fragment = _FIFTY_PAGES[probe : probe + 40].strip()

        assert fragment in rejoined, f"text at offset {probe} reached no window"
