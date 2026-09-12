"""
Item 25b: the call and turn identifiers that let one log line be tied to one
turn of one call, on both planes.

CLAUDE.md section 27 opens with the requirement and the reason: "Every log
line in a call context carries a call ID and turn ID. Without correlation
IDs, a latency problem across the two planes is undebuggable." The media
plane logs a turn's speech-to-text, turn-detection, retrieval, LLM and TTS
decisions; the control plane logs the retrieval that same turn asked it for.
Until now nothing connected the two, and within a single voice process
nothing connected a warning to the turn that produced it either - several
sessions interleave their lines in one log.

Two deliberate design points, both of which were wrong in the obvious first
attempt:

- **The context is a mutable object, not a value.** `contextvars` copies the
  *mapping* into each new task, so a `ContextVar[uuid.UUID]` set in one
  processor's task is invisible to every other processor - and Pipecat gives
  each `FrameProcessor` its own task (see `TurnMetricsRecorder`'s docstring
  for how thoroughly that has already bitten this code). Binding one shared
  `CallContext` once, before the pipeline's tasks exist, means every task
  inherits a reference to the same object, so advancing the turn is visible
  everywhere at once.

- **Stamping happens in the formatter, not at the call site.** There are
  three dozen logging statements in `apps/voice` alone, plus the lines this
  project does not write - uvicorn's, pipecat's - and "every log line" has
  to mean every line, not every line somebody remembered. `logging_setup`
  appends the stamp to whatever the handler's formatter produced, which is
  also why the identifiers survive redaction: they are added after the
  scrubber has run, so a UUID's digit runs cannot be rewritten into
  `[redacted]`.

Nothing here is required for correctness of a call. Outside a bound context
`stamp()` returns the empty string and every log line looks exactly as it
did.
"""

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

__all__ = [
    "CALL_ID_HEADER",
    "TURN_ID_HEADER",
    "CallContext",
    "bind_call_context",
    "context_from_headers",
    "current_call_context",
    "headers",
    "stamp",
    "unbind_call_context",
]

# How the identifiers cross from the media plane to the control plane. Custom
# headers rather than the W3C `traceparent`: that carries a trace and span id
# with their own sampling semantics, and pretending Norma's call/turn pair is
# one would mislead the first real tracing backend this project adopts. These
# two names say exactly what they hold.
CALL_ID_HEADER = "X-Call-Id"
TURN_ID_HEADER = "X-Turn-Id"


@dataclass
class CallContext:
    """
    One call's identity, and the turn within it that is currently being
    handled.

    `turn_id` is mutable and expected to be reassigned as turns advance -
    `TurnMetricsRecorder` owns that, so the identifier in the log is the
    same one written to the turn's `TurnMetric` row and the two can be
    joined. It starts as None because a session exists before its first
    turn does: the greeting, the config fetches and the connection log all
    belong to the call, not to any turn.
    """

    call_id: uuid.UUID
    turn_id: uuid.UUID | None = field(default=None)


_call_context: ContextVar[CallContext | None] = ContextVar(
    "norma_call_context", default=None
)


def bind_call_context(context: CallContext) -> Token[CallContext | None]:
    """
    Make `context` the current one for this task and every task it later
    creates.

    Bind before creating anything that will log - in the media plane that
    means before the pipeline is built, since Pipecat's processors take a
    snapshot of the context when their tasks are created and never see a
    later binding.
    """

    return _call_context.set(context)


def unbind_call_context(token: Token[CallContext | None]) -> None:
    """
    Restore whatever was bound before. Only meaningful for a caller that
    handles more than one call in one task - a test, or a future worker
    that multiplexes sessions - but cheap enough to always do.
    """

    _call_context.reset(token)


def current_call_context() -> CallContext | None:
    return _call_context.get()


def stamp() -> str:
    """
    The trailing correlation fragment for one log line, or "" when no call
    is in scope.

    Full UUIDs rather than short prefixes: the point of these is to join a
    media-plane line to a control-plane line and to the turn's own
    `TurnMetric` row, and a truncated identifier cannot be pasted into a
    query. The `key=value` shape matches how the rest of this codebase
    already writes identifiers into log lines (`assistant=%s`), so the same
    grep works.
    """

    context = _call_context.get()

    if context is None:
        return ""

    if context.turn_id is None:
        return f" | call={context.call_id}"

    return f" | call={context.call_id} turn={context.turn_id}"


def headers(
    *, call_id: uuid.UUID | None = None, turn_id: uuid.UUID | None = None
) -> dict[str, str]:
    """
    Correlation headers for an outgoing internal request, so the control
    plane's log lines about this turn carry the same identifiers the media
    plane's do.

    Explicit values override the ambient context, which matters for the one
    caller where the two genuinely differ: the turn-metrics POST is fired
    *after* `finish_turn()` has advanced to the next turn, so the ambient
    turn is no longer the turn being reported. Passing the record's own
    identifiers there keeps the request filed under the turn it describes.

    Returns an empty mapping outside a call, which is the correct thing to
    merge into an internal request made by a background job.
    """

    context = _call_context.get()
    resolved_call_id = call_id or (context.call_id if context else None)
    resolved_turn_id = turn_id or (context.turn_id if context else None)

    if resolved_call_id is None:
        return {}

    stamped = {CALL_ID_HEADER: str(resolved_call_id)}

    if resolved_turn_id is not None:
        stamped[TURN_ID_HEADER] = str(resolved_turn_id)

    return stamped


def context_from_headers(header_values: object) -> CallContext | None:
    """
    Rebuild the caller's context from an incoming request's headers, or None
    when there is nothing usable to rebuild.

    Accepts anything with a case-insensitive `.get` - Starlette's `Headers`,
    a plain dict in a test. Identifiers that are not UUIDs are dropped rather
    than trusted: these come from another service, and a malformed one should
    leave the log unstamped rather than stamp it with whatever arrived. They
    are correlation only and authorize nothing (the internal secret does
    that), so there is no trust decision here beyond being well-formed.
    """

    get = getattr(header_values, "get", None)

    if get is None:
        return None

    call_id = _as_uuid(get(CALL_ID_HEADER))

    if call_id is None:
        return None

    return CallContext(call_id=call_id, turn_id=_as_uuid(get(TURN_ID_HEADER)))


def _as_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None

    try:
        return uuid.UUID(value)
    except ValueError:
        return None
