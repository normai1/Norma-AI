"""
Item 24d: the loguru half of the log defences.

Pipecat logs through loguru rather than the standard library, so the
RedactingFormatter installed on stdlib handlers does not see any of it. These
tests cover the two things that matter about that: pipecat's frame-level
output stays off, and what it does emit gets scrubbed.
"""

import logging

import pytest
from loguru import logger as loguru_logger
from norma_shared.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    """Leaves global logging exactly as it was found."""

    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)

    yield

    root.handlers = original_handlers
    root.setLevel(original_level)
    loguru_logger.configure(patcher=None)


def _captured(level: str) -> list[str]:
    """Configures logging at `level` and collects what loguru emits."""

    lines: list[str] = []

    configure_logging(level)
    loguru_logger.remove()
    loguru_logger.add(lines.append, level="DEBUG")

    return lines


def test_trace_level_does_not_crash_the_process() -> None:
    """
    LOG_LEVEL is operator input and TRACE is the obvious thing to reach for,
    since loguru has that level and the standard library does not. Before the
    guard, `configure_logging("TRACE")` raised ValueError out of
    logging.basicConfig - at import, so the app would not start at all.
    """

    configure_logging("TRACE")
    configure_logging("not-a-real-level")

    assert logging.getLogger().level in (logging.DEBUG, logging.INFO)


def test_raising_log_level_does_not_switch_on_frame_logging(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Pipecat prints whole frames at TRACE, and a frame carrying a turn contains
    the caller's words verbatim. Turning application logging up to chase a bug
    must not quietly start writing conversations to disk.

    Checked through the sink configure_logging actually installed - stderr -
    rather than by inspecting loguru's internals.
    """

    configure_logging("TRACE")

    loguru_logger.trace("FRAME-LEVEL: Pushing frame with text: Wilhelmina")
    loguru_logger.debug("ORDINARY: the session started")

    written = capsys.readouterr().err

    assert "Wilhelmina" not in written
    assert "FRAME-LEVEL" not in written
    # Everything at DEBUG and above still gets through - this pins the level,
    # it does not silence the worker.
    assert "ORDINARY" in written


def test_loguru_messages_are_scrubbed() -> None:
    lines = _captured("DEBUG")

    loguru_logger.debug(
        "connecting with token=eyJhbGciOiJIUzI1NiIs.payload.signature "
        "for jo@example.com on 555-123-4567"
    )

    written = "".join(lines)

    assert "eyJhbGciOiJIUzI1NiIs" not in written
    assert "jo@example.com" not in written
    assert "555-123-4567" not in written
    assert "[redacted]" in written and "[email]" in written and "[phone]" in written


def test_loguru_timestamps_survive() -> None:
    """
    The regression that made this worth testing live rather than only in
    unit tests: the digit-run scanner ate the date and hour out of every
    formatted line.
    """

    lines = _captured("INFO")

    loguru_logger.info("session at 2026-09-07 10:24:20 handled 3 turns")

    assert "2026-09-07 10:24:20" in "".join(lines)
