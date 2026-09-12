"""
Item 25b, control-plane half: adopt the call and turn a request belongs to,
so `apps/api`'s log lines about a turn can be read alongside `apps/voice`'s.

The media plane sends `X-Call-Id` and `X-Turn-Id` on every internal request
(`norma_shared.correlation.headers`). Without this, a turn's retrieval is
logged on one side with a call id and on the other with nothing, and
CLAUDE.md section 27's stated reason for correlation IDs - "a latency
problem across the two planes is undebuggable" - still applies.

Pure ASGI rather than `BaseHTTPMiddleware` on purpose. `BaseHTTPMiddleware`
runs the downstream application in its own anyio task, and while a context
set before `call_next` is copied into that task, the arrangement is subtle
enough that it has broken across Starlette versions. A plain ASGI callable
has no task hop at all: the binding is made and undone on the very task the
endpoint runs on.

Applied to the whole app, not just the internal routes. A browser request
carries no such headers and is left exactly as it was - `context_from_headers`
returns None and nothing is bound - so there is no cost to the general case
and no route that can be added later without being covered.
"""

from norma_shared.correlation import (
    bind_call_context,
    context_from_headers,
    unbind_call_context,
)

__all__ = ["CallCorrelationMiddleware"]


class CallCorrelationMiddleware:
    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)

            return

        context = context_from_headers(_Headers(scope.get("headers") or ()))

        if context is None:
            await self._app(scope, receive, send)

            return

        token = bind_call_context(context)

        try:
            await self._app(scope, receive, send)
        finally:
            unbind_call_context(token)


class _Headers:
    """
    Case-insensitive lookup over raw ASGI header pairs.

    Small enough to spell out rather than depend on Starlette's `Headers`:
    this module is in the shared package, and the only thing it needs is
    `.get(name)` over a handful of byte pairs. Header names arrive
    lowercased per the ASGI specification, but the lookup lowercases anyway
    rather than trusting a server to have done it.
    """

    def __init__(self, raw) -> None:
        self._raw = raw

    def get(self, name: str) -> str | None:
        wanted = name.lower().encode("latin-1")

        for key, value in self._raw:
            if key.lower() == wanted:
                return value.decode("latin-1", errors="replace")

        return None
