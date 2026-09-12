"""
Item 25b: every log line a call produces says which call and which turn it
belongs to.

CLAUDE.md section 27 states the requirement and the reason - "Without
correlation IDs, a latency problem across the two planes is undebuggable" -
so the thing worth testing is not that a helper returns a string, but that
real log lines come out stamped: lines this project writes, lines pipecat
writes through loguru, and lines written from a task created after the
binding - which is how every Pipecat processor runs, and the case a
`ContextVar[uuid.UUID]` would get wrong.
"""

import asyncio
import logging
import uuid

import pytest
from norma_shared.correlation import (
    CALL_ID_HEADER,
    TURN_ID_HEADER,
    CallContext,
    bind_call_context,
    context_from_headers,
    headers,
    unbind_call_context,
)
from norma_shared.logging_setup import install_redaction

from app.turn_metrics import TurnMetricsRecorder


class _CapturingHandler(logging.Handler):
    """
    Keeps the fully formatted line, exactly as a StreamHandler would write
    it.

    Formatting has to happen at emit time, not afterwards, because that is
    when the correlation context exists - the call is over by the time a
    test inspects anything. pytest's `caplog` keeps `LogRecord`s and
    re-formats them later, which would report every line as unstamped and
    prove nothing.
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


def test_a_line_logged_inside_a_call_names_the_call_and_the_turn(
    stamped: _CapturingHandler,
) -> None:
    call_id = uuid.uuid4()
    context = CallContext(call_id=call_id)
    token = bind_call_context(context)
    recorder = TurnMetricsRecorder(call_id=call_id, call_context=context)

    try:
        logging.getLogger("test").info("retrieval took a while")
    finally:
        unbind_call_context(token)

    line = stamped.lines[0]

    assert f"call={call_id}" in line
    assert f"turn={recorder.current_turn_id()}" in line


def test_a_line_logged_outside_any_call_is_left_alone(
    stamped: _CapturingHandler,
) -> None:
    logging.getLogger("test").info("starting up")

    line = stamped.lines[0]

    assert "call=" not in line
    assert "turn=" not in line


def test_a_session_with_no_turn_yet_names_only_the_call(
    stamped: _CapturingHandler,
) -> None:
    """
    The greeting, the config fetches and the connection log all happen
    before any turn exists. They belong to the call, and a fabricated turn
    id for them would be worse than none.
    """

    call_id = uuid.uuid4()
    token = bind_call_context(CallContext(call_id=call_id))

    try:
        logging.getLogger("test").info("session started")
    finally:
        unbind_call_context(token)

    line = stamped.lines[0]

    assert f"call={call_id}" in line
    assert "turn=" not in line


def test_the_identifiers_survive_redaction_intact(
    stamped: _CapturingHandler,
) -> None:
    """
    The reason stamping happens after scrubbing rather than before.

    `redact_pii` rewrites digit runs, and a UUID is largely digit runs. A
    stamped-then-scrubbed line would carry a mangled call id that no longer
    matches the one in the database - which is worse than no id at all,
    because it looks like an answer.
    """

    call_id = uuid.uuid4()
    context = CallContext(call_id=call_id)
    token = bind_call_context(context)
    recorder = TurnMetricsRecorder(call_id=call_id, call_context=context)

    try:
        logging.getLogger("test").info("something happened")
    finally:
        unbind_call_context(token)

    line = stamped.lines[0]

    assert line.endswith(f"call={call_id} turn={recorder.current_turn_id()}")


def test_pipecats_own_loguru_lines_are_stamped_too() -> None:
    """
    Pipecat logs through loguru, which the stdlib formatter cannot reach.
    "Every log line" has to include the ones this project does not write.
    """

    from loguru import logger as loguru_logger

    # No install_redaction() here: app.main's import-time configure_logging
    # already patched loguru process-wide, and calling it again would rewrap
    # every stdlib handler in the session with nothing putting them back.
    written: list[str] = []
    sink_id = loguru_logger.add(written.append, format="{message}", level="DEBUG")
    call_id = uuid.uuid4()
    token = bind_call_context(CallContext(call_id=call_id))

    try:
        loguru_logger.info("Pushing a frame downstream")
    finally:
        unbind_call_context(token)
        loguru_logger.remove(sink_id)

    assert f"call={call_id}" in written[0]


def test_the_turn_advances_with_the_metrics_recorder() -> None:
    """
    One answer to "which turn is it", not two. The identifier in the log is
    the identifier written to that turn's TurnMetric row, so a line can be
    joined to the timings that explain it.
    """

    call_id = uuid.uuid4()
    context = CallContext(call_id=call_id)
    recorder = TurnMetricsRecorder(call_id=call_id, call_context=context)

    first = recorder.finish_turn()

    assert first.turn_id != context.turn_id
    assert context.turn_id == recorder.current_turn_id()

    second = recorder.finish_turn()

    assert second.turn_id != first.turn_id


async def test_a_turn_that_advances_in_another_task_is_still_seen_everywhere(
    stamped: _CapturingHandler,
) -> None:
    """
    The design point the obvious implementation gets wrong.

    Pipecat gives every FrameProcessor its own task, and `contextvars`
    copies the context *mapping* into each new task - so a plain
    `ContextVar[uuid.UUID]` set by one processor is invisible to the rest.
    Binding one shared mutable object instead means a turn advanced anywhere
    is seen everywhere, which is what this drives: the turn moves on in one
    task, and a line logged from a second, already-running task carries the
    new turn.
    """

    call_id = uuid.uuid4()
    context = CallContext(call_id=call_id)
    token = bind_call_context(context)
    recorder = TurnMetricsRecorder(call_id=call_id, call_context=context)
    turn_advanced = asyncio.Event()

    async def advance_the_turn() -> None:
        recorder.finish_turn()
        turn_advanced.set()

    async def log_from_another_task() -> None:
        await turn_advanced.wait()
        logging.getLogger("test").info("still in the same call")

    try:
        # Both created after the binding, as a pipeline's processors are.
        await asyncio.gather(log_from_another_task(), advance_the_turn())
    finally:
        unbind_call_context(token)

    assert stamped.lines[-1].endswith(
        f"call={call_id} turn={recorder.current_turn_id()}"
    )


def test_a_recorder_without_a_context_still_mints_turn_ids() -> None:
    """
    The correlation context is optional - every existing test constructs a
    recorder without one, and a TurnMetric row's turn id must not depend on
    logging being wired up.
    """

    recorder = TurnMetricsRecorder(call_id=uuid.uuid4())

    assert recorder.finish_turn().turn_id != recorder.finish_turn().turn_id


def test_outgoing_headers_carry_the_call_across_to_the_other_plane() -> None:
    call_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    token = bind_call_context(CallContext(call_id=call_id, turn_id=turn_id))

    try:
        sent = headers()
    finally:
        unbind_call_context(token)

    assert sent == {CALL_ID_HEADER: str(call_id), TURN_ID_HEADER: str(turn_id)}


def test_explicit_identifiers_win_over_the_ambient_ones() -> None:
    """
    The turn-metrics POST is fired after finish_turn() has already advanced
    the turn, so the ambient turn is no longer the one being reported. The
    request has to be filed under the turn it describes.
    """

    reported_call = uuid.uuid4()
    reported_turn = uuid.uuid4()
    token = bind_call_context(
        CallContext(call_id=uuid.uuid4(), turn_id=uuid.uuid4())
    )

    try:
        sent = headers(call_id=reported_call, turn_id=reported_turn)
    finally:
        unbind_call_context(token)

    assert sent == {
        CALL_ID_HEADER: str(reported_call),
        TURN_ID_HEADER: str(reported_turn),
    }


def test_no_headers_are_sent_outside_a_call() -> None:
    assert headers() == {}


def test_an_incoming_request_adopts_the_callers_identifiers() -> None:
    call_id = uuid.uuid4()
    turn_id = uuid.uuid4()

    context = context_from_headers(
        {CALL_ID_HEADER: str(call_id), TURN_ID_HEADER: str(turn_id)}
    )

    assert context == CallContext(call_id=call_id, turn_id=turn_id)


def test_a_malformed_identifier_is_dropped_rather_than_trusted() -> None:
    """
    These arrive from another service and are diagnostic only. A value that
    is not a UUID should leave the line unstamped rather than stamp it with
    whatever arrived - a log that can be written into by a caller is worse
    than one with a gap in it.
    """

    assert context_from_headers({CALL_ID_HEADER: "not-a-uuid"}) is None

    call_id = uuid.uuid4()
    context = context_from_headers(
        {CALL_ID_HEADER: str(call_id), TURN_ID_HEADER: "../../etc/passwd"}
    )

    assert context == CallContext(call_id=call_id, turn_id=None)


def test_a_request_with_no_correlation_headers_binds_nothing() -> None:
    assert context_from_headers({}) is None
