"""
Item 24d step 2: the redacting formatter.

The formatter is the backstop for log lines this project does not write - most
concretely uvicorn's access logger, which prints the WebSocket request line
verbatim and was therefore printing every voice session ticket in full.
"""

import io
import logging

import pytest
from norma_shared.logging_setup import (
    REDACTED,
    RedactingFormatter,
    install_redaction,
    scrub,
)

_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJhc3Npc3RhbnRfaWQiOiIwNzRiZTAwOCIsInR5cGUiOiJ2b2ljZV9zZXNzaW9uIn0"
    ".jYZREPOLKpSFWy7AHY79i0O96Vh6YHYsYkHWs1qQ7Yw"
)


@pytest.fixture
def captured() -> tuple[logging.Logger, io.StringIO]:
    """A logger writing through the redacting formatter into a buffer."""

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(logging.Formatter("%(message)s")))

    logger = logging.getLogger("test_log_redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    return logger, stream


def test_the_real_uvicorn_access_line_loses_its_ticket() -> None:
    """
    The exact line found in the running container's logs (see the spec).
    """

    line = (
        '172.20.0.1:57878 - "WebSocket /media/session'
        f"?ticket={_JWT}&client=v5-direct-audio\" [accepted]"
    )

    scrubbed = scrub(line)

    assert _JWT not in scrubbed
    assert "eyJ" not in scrubbed
    assert REDACTED in scrubbed
    # The rest of the line still has to be worth reading.
    assert "WebSocket /media/session" in scrubbed
    assert "client=v5-direct-audio" in scrubbed
    assert "[accepted]" in scrubbed


@pytest.mark.parametrize(
    "line",
    [
        f"Authorization: Bearer {_JWT}",
        f"connecting with token={_JWT}",
        'api_key=sk-abcdef123456789 in use',
        'password=hunter2 supplied',
        '{"secret": "s3cr3t-value"}',
    ],
)
def test_credentials_never_survive(line: str) -> None:
    scrubbed = scrub(line)

    for leaked in ("sk-abcdef123456789", "hunter2", "s3cr3t-value", _JWT):
        assert leaked not in scrubbed

    assert REDACTED in scrubbed


def test_personal_details_in_an_interpolated_argument(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    """
    Scrubbing formatted output, rather than record.msg, is what covers the
    values interpolated into %s placeholders.
    """

    logger, stream = captured
    logger.info("caller left %s and %s", "jo@example.com", "555-123-4567")

    written = stream.getvalue()

    assert "jo@example.com" not in written
    assert "555-123-4567" not in written
    assert "caller left [email] and [phone]" in written


def test_personal_details_inside_a_traceback(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    """
    A traceback is a real leak path: an httpx or asyncpg error can carry the
    request body or the statement parameters that caused it.
    """

    logger, stream = captured

    try:
        raise ValueError(f"request failed for jo@example.com with token={_JWT}")
    except ValueError:
        logger.exception("upstream call failed")

    written = stream.getvalue()

    assert "jo@example.com" not in written
    assert _JWT not in written
    assert "upstream call failed" in written
    # The traceback itself must still be there - this is a backstop, not a
    # muzzle. Losing the stack would trade one debugging problem for another.
    assert "ValueError" in written
    assert "Traceback" in written


def test_ordinary_lines_are_untouched(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured
    logger.info("session started: call=%s turns=%d", "a1b2c3", 4)

    assert "session started: call=a1b2c3 turns=4" in stream.getvalue()


def test_install_redaction_wraps_handlers_once() -> None:
    """
    Called at import and again on startup, in both apps, and possibly again
    by a test - it must be idempotent rather than nesting formatters.
    """

    logger = logging.getLogger("test_log_redaction_install")
    handler = logging.StreamHandler(io.StringIO())
    inner = logging.Formatter("%(message)s")
    handler.setFormatter(inner)
    logger.handlers = [handler]

    install_redaction()
    install_redaction()

    formatter = handler.formatter
    assert isinstance(formatter, RedactingFormatter)
    # Wrapped exactly once, around the original - uvicorn's own format string
    # and colouring have to survive.
    assert formatter._inner is inner

    logger.handlers = []


def test_a_broken_redactor_suppresses_the_line_rather_than_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If redaction itself fails, the only safe outcome is to drop the content.
    Emitting it unscrubbed would defeat the point; crashing would take out the
    caller's log call.
    """

    import norma_shared.logging_setup as logging_setup

    def _boom(_text: str) -> str:
        raise RuntimeError("redaction is broken")

    monkeypatch.setattr(logging_setup, "scrub", _boom)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(logging.Formatter("%(message)s")))

    logger = logging.getLogger("test_log_redaction_broken")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("caller left jo@example.com")

    written = stream.getvalue()

    assert "jo@example.com" not in written
    assert "redaction failed" in written

    logger.handlers = []
