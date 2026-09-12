"""
What the realtime model charges, and what a turn therefore cost (item 25b).

Prices are configuration, not constants. A published rate is a fact about a
vendor on a date, it changes without any code changing, and a stale one
baked into a release reports a margin that is quietly wrong - the failure
mode CLAUDE.md section 21 cares about, since "gross margin per minute
determines whether this business works".

So there is no default price for any model. An unpriced model records its
token counts and a null cost, which is honest, and says so in the log once
per process so the gap is visible rather than silent. Recording an unpriced
model's turns as free would be the worst of the three options: invisible,
and wrong in the flattering direction.

Only the realtime tier is priced here. The post-call tier has no consumer in
this plane (item 38, apps/worker, unbuilt), and pricing a model nothing calls
would be a setting that cannot be verified.
"""

import logging
from decimal import InvalidOperation

from norma_shared.token_cost import ModelPrice, TokenUsage, cost_micro_usd

from app import config

logger = logging.getLogger(__name__)

_warned_models: set[str] = set()


def _realtime_prices() -> dict[str, ModelPrice]:
    """
    The price table, which holds at most one entry: whichever model this
    deployment runs in the realtime tier, if its rates are configured.

    A malformed rate is treated as an unset one rather than crashing the
    process. These are operator-entered strings, and a typo in a price must
    not stop calls from being answered - the same reasoning
    `configure_logging` applies to a misspelled LOG_LEVEL.
    """

    if not (
        config.LLM_REALTIME_INPUT_USD_PER_MTOK
        and config.LLM_REALTIME_OUTPUT_USD_PER_MTOK
    ):
        return {}

    try:
        price = ModelPrice.of(
            config.LLM_REALTIME_INPUT_USD_PER_MTOK,
            config.LLM_REALTIME_OUTPUT_USD_PER_MTOK,
        )
    except (InvalidOperation, ValueError):
        logger.warning(
            "realtime model prices are not valid decimals - recording turns as "
            "unpriced: model=%s",
            config.LLM_REALTIME_MODEL,
        )

        return {}

    return {config.LLM_REALTIME_MODEL: price}


def realtime_turn_cost_micro_usd(usage: TokenUsage | None) -> int | None:
    """
    What one turn's reported usage cost, in micro-dollars, or None when
    either the provider reported no usage or the model has no configured
    price.
    """

    if usage is None:
        return None

    cost = cost_micro_usd(usage, config.LLM_REALTIME_MODEL, _realtime_prices())

    if cost is None:
        _warn_once_unpriced(config.LLM_REALTIME_MODEL)

    return cost


def _warn_once_unpriced(model: str) -> None:
    """
    Say once per process that cost cannot be computed, not once per turn -
    a warning on every turn of every call is one an operator learns to
    filter out, which is the same as not having it.
    """

    if model in _warned_models:
        return

    _warned_models.add(model)
    logger.warning(
        "no price configured for the realtime model - token counts are recorded, "
        "cost is not: model=%s",
        model,
    )
