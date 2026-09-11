"""
A long document has to be queued through the provider's per-minute token
budget, not cut short by it.

The failure this fixes, on a real 50-page PDF: FAQ generation splits the
document into windows and calls the provider once per window. Groq counts
those against one tokens-per-minute allowance, so the document spent its
budget partway through and every remaining window came back 429 - retried,
backed off, given up on. Most of the document was silently never read and
the only visible symptom was "the knowledge base only generates 8 FAQs".
"""

import json
import uuid

import pytest

from app.services.token_rate_limiter import (
    TokenRateLimiter,
    estimate_tokens,
)


class _FakeClock:
    """A clock that only moves when a sleep says it does."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _limiter(clock: _FakeClock, *, budget: int = 39_000) -> TokenRateLimiter:
    return TokenRateLimiter(budget=budget, clock=clock, sleep=clock.sleep)


async def test_calls_within_the_budget_never_wait() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)

    for _ in range(8):
        assert await limiter.acquire(4_500) == 0.0

    assert clock.slept == []


async def test_the_call_that_would_exceed_the_budget_waits() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(6_000)
    await limiter.acquire(3_000)

    # 9,000 spent; 3,000 more would be 12,000.
    waited = await limiter.acquire(3_000)

    assert waited > 0
    assert clock.slept


async def test_it_waits_only_until_enough_of_the_window_has_rolled() -> None:
    """
    Not a flat minute. The first spend expires 60s after it happened, and
    once it has there is room again - waiting longer than that is budget
    thrown away, which on a twelve-window document is minutes of it.
    """

    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(6_000)
    clock.now += 50.0
    await limiter.acquire(4_000)

    # Full. The 6,000 leaves the window 10s from now, not 60.
    waited = await limiter.acquire(5_000)

    assert waited == pytest.approx(10.0)


async def test_the_window_is_rolling_not_a_fixed_reset() -> None:
    """
    A fixed once-a-minute reset would allow a full budget at 59s and another
    at 61s - exactly the burst the provider is measuring against.
    """

    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(10_000)
    clock.now += 59.0

    waited = await limiter.acquire(10_000)

    assert waited == pytest.approx(1.0)


async def test_spend_older_than_the_window_stops_counting() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(10_000)
    clock.now += 61.0

    assert await limiter.acquire(10_000) == 0.0


async def test_a_whole_document_gets_through_rather_than_being_refused() -> None:
    """
    The headline behaviour. Twelve windows at roughly 4,900 tokens each is
    58,800 - half again the minute's budget - and every one of them must be
    sent, just not all at once.
    """

    clock = _FakeClock()
    limiter = _limiter(clock)

    for _ in range(12):
        await limiter.acquire(4_900)

    # Nothing refused, and it took more than one minute's worth of waiting.
    assert sum(clock.slept) > 0
    assert clock.now - 1000.0 < 120.0


async def test_a_request_larger_than_the_whole_budget_still_proceeds() -> None:
    """
    It can never fit, so refusing it forever would lose that window for good.
    Waiting for the window to clear is the closest thing to honouring the
    limit.
    """

    clock = _FakeClock()
    limiter = _limiter(clock, budget=1_000)

    await limiter.acquire(500)
    waited = await limiter.acquire(5_000)

    assert waited > 0


async def test_actual_usage_replaces_the_estimate() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(5_000)
    limiter.record_actual(estimated=5_000, actual=9_000)

    # 9,000 of 10,000 spent, so 2,000 no longer fits.
    assert await limiter.acquire(2_000) > 0


async def test_recording_actual_usage_ignores_a_mismatched_estimate() -> None:
    """
    Only ever corrects the entry it was told about. A late correction that
    overwrote whatever happened to be last would corrupt another call's
    accounting.
    """

    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(5_000)
    limiter.record_actual(estimated=1_234, actual=9_000)

    assert await limiter.acquire(5_000) == 0.0


@pytest.mark.parametrize(
    ("text", "at_least"),
    [
        ("", 0),
        ("hello", 1),
        ("a" * 12_000, 3_000),
    ],
)
def test_estimating_never_returns_zero_for_real_text(text: str, at_least: int) -> None:
    assert estimate_tokens(text) >= at_least


def test_the_estimate_errs_high_rather_than_low() -> None:
    """
    Under-estimating spends budget that is not there and earns the 429 this
    exists to avoid, so the characters-per-token constant sits below the
    usual rule of thumb of four.
    """

    assert estimate_tokens("a" * 4_000) > 1_000


async def test_generation_covers_a_long_document_instead_of_stopping_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    End to end through faq_generation, with the provider refusing anything
    that arrives over budget the way the real one does.

    Before the limiter this is exactly what happened to a 50-page PDF: the
    first few windows answered, the budget ran out, and every window after
    it was refused and eventually abandoned. The document is unchanged; only
    the queueing is new.
    """

    from app.core.config import settings
    from app.providers.llm import LLMProviderUnavailable
    from app.services import faq_generation

    clock = _FakeClock()
    budget = 20_000
    monkeypatch.setattr(settings, "faq_generation_tokens_per_minute", budget)
    monkeypatch.setattr(
        faq_generation,
        "TokenRateLimiter",
        lambda **kwargs: TokenRateLimiter(
            budget=kwargs["budget"], clock=clock, sleep=clock.sleep
        ),
    )

    class _BudgetEnforcingProvider:
        """Refuses any call whose tokens do not fit the minute, as Groq does."""

        def __init__(self) -> None:
            self.answered = 0
            self.refused = 0
            self._spent: list[tuple[float, int]] = []

        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            cost = estimate_tokens(system_prompt) + estimate_tokens(user_prompt) + 1_200
            self._spent = [
                (at, n) for at, n in self._spent if at > clock.now - 60.0
            ]

            if sum(n for _at, n in self._spent) + cost > budget:
                self.refused += 1
                raise LLMProviderUnavailable("429")

            self._spent.append((clock.now, cost))
            self.answered += 1

            # A bare list, which is the shape _extract_pairs accepts. An
            # earlier version of this fixture wrapped it in an object, so
            # every window "succeeded" and produced no pairs - which the
            # test could not see, because it only counted calls.
            return json.dumps(
                [
                    {
                        "question": f"Question {self.answered}-{i}?",
                        "answer": f"Answer {self.answered}-{i}.",
                    }
                    for i in range(3)
                ]
            )

    provider = _BudgetEnforcingProvider()
    # Roughly a 50-page PDF: 12 windows of 12,000 characters.
    text = " ".join(f"word{i}" for i in range(26_000))

    windows = faq_generation._windows(text)
    assert len(windows) >= 10, "the fixture is not a long document"

    already: list[str] = []
    limiter = TokenRateLimiter(budget=budget, clock=clock, sleep=clock.sleep)

    for window in windows:
        pairs = await faq_generation._generate_for_window(
            provider,
            window,
            already_asked=already,
            knowledge_source_id=uuid.uuid4(),
            rate_limiter=limiter,
        )
        already.extend(question for question, _answer in pairs)

    assert provider.refused == 0
    assert provider.answered == len(windows)
    # Every window actually contributed, rather than merely being called.
    assert len(already) == 3 * len(windows)


async def test_the_provider_s_own_budget_overrides_the_configured_one() -> None:
    """
    The bug that made this necessary: pacing against a configured 39,000
    tokens per minute while the account's real allowance for the model was
    8,000, so three windows of a fifty-page document were refused and
    abandoned.
    """

    from app.providers.llm import TokenBudget

    clock = _FakeClock()
    limiter = _limiter(clock, budget=39_000)

    await limiter.acquire(5_000)
    limiter.adopt_provider_budget(TokenBudget(limit=8_000, remaining=3_000))

    # 8,000 limit with 3,000 left means 5,000 more does not fit.
    assert await limiter.acquire(5_000) > 0


async def test_a_provider_that_reports_nothing_leaves_the_budget_alone() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    limiter.adopt_provider_budget(None)

    assert await limiter.acquire(10_000) == 0.0


async def test_a_partial_report_uses_what_it_has() -> None:
    """
    A provider may name its limit without saying what is left, or the other
    way round. Neither should be read as zero.
    """

    from app.providers.llm import TokenBudget

    clock = _FakeClock()
    limiter = _limiter(clock, budget=39_000)

    limiter.adopt_provider_budget(TokenBudget(limit=8_000, remaining=None))

    assert await limiter.acquire(8_000) == 0.0
    assert await limiter.acquire(1) > 0


async def test_remaining_headroom_is_trusted_over_local_accounting() -> None:
    """
    Estimates drift; the provider's count is the one that decides whether
    the next call is refused. If it says there is room, there is room.
    """

    from app.providers.llm import TokenBudget

    clock = _FakeClock()
    limiter = _limiter(clock, budget=10_000)

    await limiter.acquire(10_000)
    limiter.adopt_provider_budget(TokenBudget(limit=10_000, remaining=9_000))

    assert await limiter.acquire(5_000) == 0.0


async def test_the_wait_lines_up_with_the_provider_s_own_reset() -> None:
    """
    The provider says when its allowance refills. Ignoring that and starting
    a fresh minute on every reconciliation re-serves time the provider had
    already served: measured on a ten-window document, six minutes of
    unavoidable waiting became seventeen and a half.
    """

    from app.providers.llm import TokenBudget

    clock = _FakeClock()
    limiter = _limiter(clock, budget=8_000)

    # Spent out, but the provider says it refills in 10 seconds.
    limiter.adopt_provider_budget(
        TokenBudget(limit=8_000, remaining=0, reset_seconds=10.0)
    )

    assert await limiter.acquire(5_000) == pytest.approx(10.0)


async def test_without_a_reset_time_it_assumes_a_fresh_window() -> None:
    """
    The conservative direction: waiting too long rather than earning a
    refusal.
    """

    from app.providers.llm import TokenBudget

    clock = _FakeClock()
    limiter = _limiter(clock, budget=8_000)

    limiter.adopt_provider_budget(
        TokenBudget(limit=8_000, remaining=0, reset_seconds=None)
    )

    assert await limiter.acquire(5_000) == pytest.approx(60.0)


async def test_a_whole_document_is_covered_at_close_to_the_provider_s_own_pace() -> (
    None
):
    """
    The requirement in one test: every window of a long document is sent,
    and how long it takes is set by the provider's allowance rather than by
    waiting badly.

    The window size is chosen to divide the minute (window_chars_for_budget),
    so a document's cost in tokens divided by the per-minute budget is the
    time it should take. An earlier version sent 12,000-character windows
    costing 4,700 tokens against an 8,000 budget - one per minute, 41% of
    every minute wasted - and took 620 seconds against a 423-second floor.
    """

    from app.providers.llm import TokenBudget
    from app.services.faq_generation import (
        _per_call_overhead_tokens,
        window_chars_for_budget,
    )

    clock = _FakeClock()
    budget = 8_000
    limiter = _limiter(clock, budget=budget)

    chars = window_chars_for_budget(budget)
    # Everything a call really costs, not just its text.
    per_window = estimate_tokens("x" * chars) + _per_call_overhead_tokens()

    # A fifty-page PDF is roughly 143,000 characters.
    windows = -(-143_000 // chars)
    started = clock.now

    for _ in range(windows):
        await limiter.acquire(per_window)
        limiter.adopt_provider_budget(
            TokenBudget(
                limit=budget,
                remaining=max(0, budget - limiter._spent_now(clock.now)),
                reset_seconds=60.0 - min(60.0, clock.now % 60.0),
            )
        )

    elapsed = clock.now - started

    # A window is indivisible, so the reachable floor is how many fit in a
    # minute, not the arithmetic ratio of tokens to budget. At 12,000
    # characters a window costs 5,748 of an 8,000 allowance: one per minute,
    # with the remaining 2,252 unusable by anything smaller that would not
    # cost more in total. Dividing tokens by budget would demand 517s for
    # work that cannot be done in less than 660.
    per_minute = max(1, budget // per_window)
    floor = (windows - 1) / per_minute * 60.0

    assert elapsed <= floor + 60.0, (
        f"{windows} windows took {elapsed:.0f}s against a {floor:.0f}s floor - "
        "the pacing is wasting time the provider was not asking for"
    )
    # And it really is pacing, not racing: a document this size cannot be
    # sent inside a single minute.
    assert elapsed >= floor - 60.0
