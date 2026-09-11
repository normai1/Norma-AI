"""
A token-per-minute budget for LLM calls, so a long document is queued
through the provider's rate limit instead of being cut short by it.

The problem this solves, seen on a real 50-page PDF: FAQ generation splits a
document into windows and calls the provider once per window. Groq's limit
for this model is counted in tokens per minute across all of them, so a
document large enough to matter spends its whole budget partway through and
every remaining window comes back 429. Before this, that was handled by
retrying with backoff and then giving up on the window - which is to say
most of the document was silently never read, and the only visible symptom
was a short FAQ list.

Waiting is the right answer here and would be the wrong answer in a call.
This runs in the background after an upload, where a document that finishes
in three minutes instead of one is not a problem anyone can perceive; the
audio path's own provider limits are handled by failing fast instead
(CLAUDE.md section 9: silence is the worst possible failure).

The window is rolling rather than fixed: spend is recorded with its
timestamp and anything older than a minute stops counting. A fixed
once-a-minute reset would allow a full budget at 59s and another at 61s,
which is exactly the burst the provider is measuring against.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0

# Roughly how many characters of English one token covers. Used only to
# estimate a request's cost before it is sent, since the provider will not
# say until afterwards. Deliberately conservative - four is the usual rule of
# thumb and this text is ordinary prose, but under-estimating spends budget
# that is not there and earns the 429 this exists to avoid.
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """
    A cost estimate for `text`, in tokens. Never zero for non-empty text.
    """

    if not text:
        return 0

    return max(1, int(len(text) / CHARS_PER_TOKEN) + 1)


class TokenRateLimiter:
    """
    Lets callers spend up to `budget` tokens per rolling minute, making them
    wait when the next request would exceed it.

    One limiter is one budget, so callers sharing a provider must share the
    instance for it to mean anything.
    """

    def __init__(
        self,
        *,
        budget: int,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self._budget = budget
        self._window_seconds = window_seconds
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._spent: deque[tuple[float, int]] = deque()
        # Serialises waiters, so two callers cannot each look at the same
        # free budget and both decide they fit in it.
        self._lock = asyncio.Lock()

    def _forget_expired(self, now: float) -> None:
        cutoff = now - self._window_seconds

        while self._spent and self._spent[0][0] <= cutoff:
            self._spent.popleft()

    def _spent_now(self, now: float) -> int:
        self._forget_expired(now)

        return sum(tokens for _at, tokens in self._spent)

    def _seconds_until_room_for(self, tokens: int, now: float) -> float:
        """
        How long until `tokens` would fit, assuming nothing else is spent.
        Zero when they already fit.
        """

        spent = self._spent_now(now)

        if spent + tokens <= self._budget:
            return 0.0

        # Expire the oldest entries, in order, until enough budget is freed.
        # The wait is until whichever one crosses that line leaves the
        # window - not a fixed sleep, which would either overshoot or need
        # to be re-checked in a loop.
        freed = 0

        for at, spent_tokens in self._spent:
            freed += spent_tokens

            if spent - freed + tokens <= self._budget:
                return max(0.0, (at + self._window_seconds) - now)

        # More than a whole window's budget in one request. It can never
        # fit, so waiting for the window to clear is the closest thing to
        # honouring it; the caller is told, because this means a window of
        # the document is larger than the limit allows in one call.
        return max(0.0, self._window_seconds - (now - self._spent[0][0]))

    async def acquire(self, tokens: int) -> float:
        """
        Wait until `tokens` fit in the current window, record them as spent,
        and return how long the wait was.

        Recording on the way out rather than on the way back means a request
        counts against the budget from the moment it is sent, which is when
        the provider starts counting it too.
        """

        async with self._lock:
            now = self._clock()
            wait = self._seconds_until_room_for(tokens, now)

            if wait > 0:
                logger.info(
                    "token budget reached - waiting %.1fs before the next "
                    "call (%d tokens queued against a %d/min budget)",
                    wait,
                    tokens,
                    self._budget,
                )

                await self._sleep(wait)
                now = self._clock()
                self._forget_expired(now)

            self._spent.append((now, tokens))

            return wait

    def adopt_provider_budget(self, budget) -> None:
        """
        Take the provider's own numbers over the configured ones.

        A configured budget is a guess that cannot notice being wrong. This
        one was: FAQ generation paced itself against 39,000 tokens per
        minute while the account's real allowance for the model was 8,000,
        and three windows of a fifty-page document were refused and
        abandoned - the same symptom the pacing existed to fix. The provider
        reports its limit on every response, so after the first call there
        is no reason to be guessing at all.

        `budget` is a providers.llm.TokenBudget, or None from a provider
        that reports nothing, in which case the configured value stands.
        """

        if budget is None:
            return

        if budget.limit is not None and budget.limit > 0:
            if budget.limit != self._budget:
                logger.info(
                    "adopting the provider's own token budget: %d/min "
                    "(was pacing against %d/min)",
                    budget.limit,
                    self._budget,
                )

            self._budget = budget.limit

        if budget.remaining is None:
            return

        # Reconcile against what the provider says is left rather than what
        # this thinks it spent. Estimates drift; the provider's count is the
        # one that decides whether the next call is refused.
        spent = max(0, self._budget - budget.remaining)
        now = self._clock()
        self._spent.clear()

        if not spent:
            return

        # Backdate the entry so it leaves this window exactly when the
        # provider says its own allowance refills. Recording it at `now`
        # instead makes every reconciliation restart a full minute that the
        # provider had already partly served: measured on a ten-window
        # document, that turned six minutes of unavoidable waiting into
        # seventeen and a half.
        #
        # Without a reset time there is nothing better than assuming a fresh
        # window, which is the conservative direction - it waits too long
        # rather than earning a refusal.
        reset = budget.reset_seconds

        if reset is None:
            self._spent.append((now, spent))

            return

        self._spent.append((now - (self._window_seconds - reset), spent))

    def record_actual(self, estimated: int, actual: int) -> None:
        """
        Correct the most recent entry once the provider says what a call
        really cost.

        The estimate is a character count divided by a constant, so it is
        wrong on every call; leaving it wrong in the same direction all
        document long is what would eventually walk into the 429 this
        exists to prevent.
        """

        if not self._spent or actual <= 0:
            return

        at, recorded = self._spent[-1]

        if recorded != estimated:
            return

        self._spent[-1] = (at, actual)
