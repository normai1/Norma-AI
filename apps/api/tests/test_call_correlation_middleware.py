"""
Item 25b, control-plane half: a request made on behalf of a call is logged
under that call.

CLAUDE.md section 27: "Without correlation IDs, a latency problem across the
two planes is undebuggable." The media plane logs a turn's retrieval request;
this plane logs the retrieval itself. The two are only readable together if
the identifiers cross the boundary, which is what these exercise - through a
real request against the real app, since the thing that can silently break is
the middleware being registered in the wrong place or not at all.
"""

import logging
import uuid

import pytest
from httpx import AsyncClient
from norma_shared.correlation import CALL_ID_HEADER, TURN_ID_HEADER
from norma_shared.logging_setup import install_redaction

_HEALTH_URL = "/api/v1/health"


class _CapturingHandler(logging.Handler):
    """
    Keeps each line as a StreamHandler would have written it.

    Formatted at emit time, not afterwards: the binding is gone by the time
    the request returns, so re-formatting a stored record later would report
    every line as unstamped and prove nothing.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def stamped():
    """
    A handler wrapped by the same formatter the running app installs.

    `install_redaction` wraps *every* handler in the process, pytest's own
    included, and it is not reversible on its own - so every formatter is
    snapshotted and put back afterwards. Leaving it installed leaks into the
    rest of the session: later tests that read `caplog.text` would then be
    reading scrubbed output, and a case built around a quoted price or a
    phone number fails for reasons that have nothing to do with it. Found
    exactly that way.
    """

    handler = _CapturingHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    everything = [root, *(
        existing
        for existing in logging.Logger.manager.loggerDict.values()
        if isinstance(existing, logging.Logger)
    )]
    formatters = [
        (existing_handler, existing_handler.formatter)
        for logger in everything
        for existing_handler in logger.handlers
    ]

    install_redaction()

    try:
        yield handler
    finally:
        for wrapped_handler, formatter in formatters:
            wrapped_handler.setFormatter(formatter)

        root.removeHandler(handler)
        root.setLevel(previous_level)


async def test_a_request_made_for_a_call_is_logged_under_that_call(
    client: AsyncClient, stamped: _CapturingHandler
) -> None:
    call_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    logger = logging.getLogger("app.test")

    async def _log_during_request() -> None:
        logger.info("retrieved 5 chunks")

    # A route that logs is what is actually being tested, so log from inside
    # the request rather than trusting that some route happens to.
    from app.main import app

    @app.get("/__correlation_probe")
    async def _probe() -> dict[str, bool]:
        await _log_during_request()

        return {"ok": True}

    try:
        response = await client.get(
            "/__correlation_probe",
            headers={CALL_ID_HEADER: str(call_id), TURN_ID_HEADER: str(turn_id)},
        )
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__correlation_probe"
        ]

    assert response.status_code == 200

    probe_lines = [line for line in stamped.lines if "retrieved 5 chunks" in line]

    assert probe_lines
    assert probe_lines[0].endswith(f"call={call_id} turn={turn_id}")


async def test_an_ordinary_browser_request_is_left_alone(
    client: AsyncClient, stamped: _CapturingHandler
) -> None:
    """
    Every control-plane request goes through this middleware, not only the
    internal ones. A request with no correlation headers - which is every
    request from the web app - must be logged exactly as it was before.
    """

    logger = logging.getLogger("app.test")

    from app.main import app

    @app.get("/__uncorrelated_probe")
    async def _probe() -> dict[str, bool]:
        logger.info("listing assistants")

        return {"ok": True}

    try:
        await client.get("/__uncorrelated_probe")
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__uncorrelated_probe"
        ]

    probe_lines = [line for line in stamped.lines if "listing assistants" in line]

    assert probe_lines
    assert "call=" not in probe_lines[0]


async def test_a_header_that_is_not_a_uuid_leaves_the_line_unstamped(
    client: AsyncClient, stamped: _CapturingHandler
) -> None:
    """
    These identifiers come from another service and authorize nothing, but a
    log an unauthenticated caller can write arbitrary text into is worse
    than one with a gap. A malformed value is dropped, not echoed.
    """

    logger = logging.getLogger("app.test")

    from app.main import app

    @app.get("/__malformed_probe")
    async def _probe() -> dict[str, bool]:
        logger.info("handling a request")

        return {"ok": True}

    try:
        await client.get(
            "/__malformed_probe",
            headers={CALL_ID_HEADER: "call=injected turn=injected"},
        )
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__malformed_probe"
        ]

    probe_lines = [line for line in stamped.lines if "handling a request" in line]

    assert probe_lines
    assert "injected" not in probe_lines[0]


async def test_the_health_endpoint_still_works_behind_the_middleware(
    client: AsyncClient,
) -> None:
    """
    The middleware wraps every request, including ones with no correlation
    headers at all. A mistake in it would take the whole API down, so the
    cheapest possible end-to-end check is worth keeping.
    """

    assert (await client.get(_HEALTH_URL)).status_code == 200
