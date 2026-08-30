"""
Authentication for service-to-service calls from apps/voice (item 20b).
There is no user session inside a live call for a JWT to belong to - the
shared secret itself is the trust boundary, the same reasoning telephony
webhook signature verification already establishes for a different kind
of unauthenticated-by-session caller.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


def require_internal_secret(
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> None:
    """
    Raise 401 unless X-Internal-Secret matches the configured
    INTERNAL_API_SECRET. Uses a constant-time comparison - this secret
    guards every internal route, so a timing side-channel here is exactly
    the class of leak CLAUDE.md's security rules exist to close off.
    """

    if x_internal_secret is None or not hmac.compare_digest(
        x_internal_secret, settings.internal_api_secret
    ):
        raise _UNAUTHORIZED


RequireInternalSecret = Annotated[None, Depends(require_internal_secret)]
