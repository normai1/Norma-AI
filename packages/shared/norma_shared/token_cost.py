"""
Item 25b: what a turn cost, in tokens and in money.

CLAUDE.md section 21 is unusually direct about why this is not a
post-launch concern: "Capture provider cost per call from day one. Gross
margin per minute determines whether this business works." Section 27 lists
"token usage and provider cost per call" among the signals that must be
observable. Neither is answerable today - nothing reads the usage the LLM
providers already return.

Money is held as an integer number of **micro-dollars** (10^-6 USD), never a
float. A realtime turn costs on the order of a hundredth of a cent, calls are
summed into invoices, and binary floating point cannot represent a tenth of a
cent exactly - the one place in this codebase where that difference ends up
in front of a customer. Integers at this scale also stay exact well past any
plausible organization's monthly total.

The price table is configuration, not a constant, and it is deliberately
incomplete: a model nobody has priced yields None rather than a guess, and a
None cost is recorded as unknown rather than as zero. Reporting an unpriced
model as free is the failure mode worth designing against - it is invisible,
and it reports the margin as better than it is.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["ModelPrice", "TokenUsage", "cost_micro_usd"]

_MICRO_USD_PER_USD = Decimal(1_000_000)
_TOKENS_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class TokenUsage:
    """
    One provider call's token counts, as the provider itself reported them.

    Reported, not estimated: `apps/api`'s own `estimate_tokens` exists to
    stay *under* a rate limit before a call is made, where guessing high is
    the safe direction. Cost is the opposite problem - a number that will be
    compared against an invoice - so nothing here is ever derived from a
    character count.
    """

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ModelPrice:
    """
    What one model charges, in USD per million tokens, priced separately for
    input and output because every provider prices them differently.

    Decimal rather than float, for the reason in the module docstring, and
    accepting str so a price can be written in configuration exactly as the
    provider publishes it ("0.15") without a float ever existing.
    """

    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    @classmethod
    def of(
        cls, input_usd_per_million: str, output_usd_per_million: str
    ) -> "ModelPrice":
        return cls(
            input_usd_per_million=Decimal(input_usd_per_million),
            output_usd_per_million=Decimal(output_usd_per_million),
        )


def cost_micro_usd(
    usage: TokenUsage | None,
    model: str,
    prices: Mapping[str, ModelPrice],
) -> int | None:
    """
    What `usage` cost on `model`, in micro-dollars, or None when the answer
    is genuinely unknown.

    None means one of two honest things: the provider did not report usage,
    or nobody has priced this model. Both are distinguishable from a real
    zero, which is what a free tier or an empty reply would produce, and
    that distinction is the point - see the module docstring.

    Rounded half-up to the micro-dollar. At these rates a single turn is
    hundreds of micro-dollars, so rounding is a rounding of the last digit,
    not of the answer.
    """

    if usage is None:
        return None

    price = prices.get(model)

    if price is None:
        return None

    usd = (
        Decimal(usage.prompt_tokens) * price.input_usd_per_million
        + Decimal(usage.completion_tokens) * price.output_usd_per_million
    ) / _TOKENS_PER_MILLION

    return int((usd * _MICRO_USD_PER_USD).quantize(Decimal(1), rounding=ROUND_HALF_UP))
