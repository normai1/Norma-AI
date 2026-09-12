"""
The headers every apps/voice -> apps/api internal request carries.

Two things travel together and had no single place to be assembled: the
shared secret that authorizes the request, and item 25b's call/turn
correlation identifiers that let the control plane's log lines about a turn
be found next to the media plane's. Six clients were each writing the
secret header inline; adding a second header to each of them by hand is how
one of them ends up without it.
"""

import uuid

from norma_shared.correlation import headers as correlation_headers

from app import config


def internal_headers(
    *, call_id: uuid.UUID | None = None, turn_id: uuid.UUID | None = None
) -> dict[str, str]:
    """
    Authorization plus correlation for one internal request.

    The correlation half is best-effort by design: outside a bound call
    context it contributes nothing and the request is made exactly as
    before. Nothing on the API side may depend on these being present -
    they are for reading logs, and they authorize nothing.

    call_id/turn_id override the ambient context for the one caller whose
    request outlives the turn it describes - see
    `norma_shared.correlation.headers`.
    """

    return {
        "X-Internal-Secret": config.INTERNAL_API_SECRET,
        **correlation_headers(call_id=call_id, turn_id=turn_id),
    }
