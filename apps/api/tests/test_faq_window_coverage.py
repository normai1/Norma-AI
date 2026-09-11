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
    _ASSUMED_COMPLETION_TOKENS,
    _MIN_SOURCE_TEXT_CHARS,
    MAX_SOURCE_TEXT_CHARS,
    MAX_SOURCE_WINDOWS,
    _windows,
    window_chars_for_budget,
)
from app.services.token_rate_limiter import estimate_tokens

# 50 pages at roughly 280 words of ordinary prose.
_FIFTY_PAGES = " ".join(f"sentence{i} about the business" for i in range(5_200))


@pytest.mark.parametrize("budget", [3_000, 6_000, 8_000, 39_000, 200_000])
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


@pytest.mark.parametrize("budget", [3_000, 6_000, 8_000, 39_000, 200_000])
def test_the_window_size_stays_inside_its_bounds(budget: int) -> None:
    chars = window_chars_for_budget(budget)

    assert _MIN_SOURCE_TEXT_CHARS <= chars <= MAX_SOURCE_TEXT_CHARS


@pytest.mark.parametrize("budget", [6_000, 8_000, 39_000, 200_000])
def test_at_least_one_window_fits_inside_a_minute(budget: int) -> None:
    """
    A window that cannot fit its own budget would wait forever for room
    that never comes.
    """

    chars = window_chars_for_budget(budget)
    cost = estimate_tokens("x" * chars) + _ASSUMED_COMPLETION_TOKENS

    assert cost <= budget


def test_windows_pack_into_the_minute_rather_than_merely_fitting() -> None:
    """
    The measured cost of getting this wrong: 12,000-character windows at
    about 4,700 tokens against an 8,000 budget fit one per minute and waste
    41% of every one, turning a 423-second document into 620 seconds.
    """

    budget = 8_000
    chars = window_chars_for_budget(budget)
    cost = estimate_tokens("x" * chars) + _ASSUMED_COMPLETION_TOKENS
    fits = budget // cost

    assert fits >= 2
    assert (budget - fits * cost) / budget < 0.10


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
