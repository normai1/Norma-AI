"""
Item 24d: a last line of defence between the application's logs and anything
that should never be in them.

CLAUDE.md section 27 lists what must never be logged - passwords, API keys,
tokens, transcript text, caller PII. Sections 27 and 36 state the rule; this
module is what makes a violation harmless when someone writes one anyway. It
scrubs every record on its way out, including records this project does not
write itself.

That last part is the reason it exists at all rather than staying a code-review
convention. Every voice session's ticket - a bearer credential good for a media
session on a named assistant - was being written to the log in full, by
uvicorn's access logger, from a request line no Norma code formats:

    "WebSocket /media/session?ticket=eyJhbGciOiJIUzI1NiIs..." [accepted]

No amount of care in Norma's own logging statements would have caught that.

**This is a backstop, not permission.** It matches patterns; it cannot
recognise a plain sentence of caller speech. Turn-path code still logs word
counts and identifiers, never utterances, and `redact_pii` does not change
that obligation.
"""

import logging
import re
import sys
from collections.abc import Iterable

from norma_shared.pii import redact_pii

REDACTED = "[redacted]"

# A credential passed by name, in a query string, header dump, or key=value
# log line. The value runs to the next separator so the rest of the line
# survives - a URL keeps its remaining parameters, which is what makes the
# scrubbed line still worth reading.
_NAMED_CREDENTIAL = re.compile(
    r"\b(ticket|token|secret|password|passwd|api[_-]?key|access[_-]?key|authorization|auth)"
    r"(=|:\s*|\"?\s*:\s*\"?)"
    r"([^&\s\"',)]+)",
    re.IGNORECASE,
)

# A bare JWT, for the case where the credential arrives without a name in
# front of it - an Authorization header value, or a token logged on its own.
_BARE_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def scrub(text: str) -> str:
    """
    Remove credentials and personal details from one line of log output.

    Credentials go first: a JWT's payload is base64 and can contain digit runs
    that would otherwise be rewritten by the PII pass, leaving a mangled token
    in the log instead of no token at all.
    """

    scrubbed = _BARE_JWT.sub(REDACTED, text)
    scrubbed = _NAMED_CREDENTIAL.sub(rf"\1\2{REDACTED}", scrubbed)

    return redact_pii(scrubbed)


class RedactingFormatter(logging.Formatter):
    """
    Wraps another formatter and scrubs whatever it produces.

    Wrapping rather than replacing is deliberate: uvicorn's access and default
    formatters carry their own format string and colouring, and swapping them
    out would change the shape of every line the operator is used to reading.
    Only the text changes.

    Scrubbing the *formatted* output - rather than filtering `record.msg` -
    is what covers interpolated `%s` arguments and exception tracebacks in one
    place. A traceback is a real leak path: an httpx or asyncpg error can carry
    the request body or the statement parameters that caused it.
    """

    def __init__(self, inner: logging.Formatter) -> None:
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        formatted = self._inner.format(record)

        try:
            return scrub(formatted)
        except Exception:  # pragma: no cover - defensive
            # Losing a log line to a redaction bug would be worse than the bug.
            # An unscrubbed line is not an option either, so drop the content
            # and keep the fact that something was logged.
            return f"{record.levelname} | log line suppressed: redaction failed"


def _handlers(loggers: Iterable[logging.Logger]) -> Iterable[logging.Handler]:
    seen: set[int] = set()

    for logger in loggers:
        for handler in logger.handlers:
            if id(handler) not in seen:
                seen.add(id(handler))
                yield handler


def _install_loguru_redaction() -> None:
    """
    Route loguru's messages through the same scrubber.

    Pipecat logs through loguru, not the standard library, so nothing above
    touches it. A patcher rewrites `record["message"]` and leaves loguru's
    sinks, level, and format alone, so pipecat's output looks exactly as it
    did.

    Two limits worth stating rather than glossing over:

    - It scrubs *patterns* - credentials and personal details appearing in
      pipecat's own messages, such as a provider URL carrying a token. Like
      the stdlib path, it cannot recognise a plain sentence of speech.
    - A loguru exception traceback renders from `record["exception"]`, not
      from the message, so it is not covered. The stdlib path handles
      tracebacks; this one handles messages.

    That first limit matters here because pipecat's frame-push lines print a
    frame's full payload, and the frames carrying a conversation say so in
    full:

        Pushing OutputTransportMessageUrgentFrame#7(message: {'type':
        'transcript', 'text': "<everything the caller just said>"}) downstream

    Those lines are TRACE. Nothing in this project enables TRACE, and
    `configure_logging` pins loguru at DEBUG below so that an operator raising
    LOG_LEVEL cannot switch conversation logging on by accident. Reaching them
    takes a deliberate `loguru.logger.add(..., level="TRACE")`, and anyone
    writing that line is choosing to log the call.
    """

    try:
        from loguru import logger as loguru_logger
    except ImportError:  # apps that do not use pipecat
        return

    loguru_logger.configure(patcher=lambda record: record.update(message=scrub(record["message"])))


def _pin_loguru_level(level: str) -> None:
    """
    Replace loguru's default stderr sink with one at `level`.

    `level` has already been resolved to a standard-library level by
    configure_logging, so it can never be TRACE - which is the point: pipecat's
    frame-level output, transcripts included, stays off.
    """

    try:
        from loguru import logger as loguru_logger
    except ImportError:
        return

    loguru_logger.remove()
    loguru_logger.add(sys.stderr, level=level)


def install_redaction() -> None:
    """
    Wrap the formatter of every handler currently installed, on the root
    logger and on every named logger, and patch loguru alongside them.

    Safe to call more than once - an already-wrapped handler is left alone -
    and safe to call again later, which matters because uvicorn installs its
    own handlers at server start. Both apps call it at import and again on
    startup so it applies whichever order those happen in.
    """

    _install_loguru_redaction()

    manager_loggers = [
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]

    for handler in _handlers([logging.getLogger(), *manager_loggers]):
        if isinstance(handler.formatter, RedactingFormatter):
            continue

        handler.setFormatter(RedactingFormatter(handler.formatter or logging.Formatter()))


def configure_logging(level: str = "INFO", *, fmt: str = _DEFAULT_FORMAT) -> None:
    """
    Set up stdlib logging for an app process, with redaction installed.

    Without the basicConfig call the root logger stays at WARNING with no
    handlers, so every logger.info() in the app is silently discarded - found
    the hard way during a barge-in investigation that produced an entirely
    empty log for a call that had definitely happened.
    """

    resolved = level.upper()

    # LOG_LEVEL is operator input, and the standard library raises on a level
    # it does not know - "TRACE" being the obvious one to reach for, since
    # loguru has it and the standard library does not. A misspelled level must
    # not stop the process from starting.
    if resolved not in logging.getLevelNamesMapping():
        resolved = "DEBUG" if resolved == "TRACE" else "INFO"

    logging.basicConfig(level=resolved, format=fmt)

    # LOG_LEVEL raises the stdlib level only. Pipecat's loguru logger stays at
    # DEBUG whatever it is set to, because the level below DEBUG is where
    # pipecat prints whole frames - transcripts included (see
    # _install_loguru_redaction). Turning up application logging to chase a
    # bug must not quietly start writing callers' conversations to disk.
    _pin_loguru_level(resolved)

    install_redaction()
