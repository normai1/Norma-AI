"""
LangSmith tracing for one turn's retrieval (item 25a).

The logging added in 25a answers "what did retrieval decide" one line at a
time: counts, scores, source ids. That is enough to tell a wrong answer from
wrong retrieval, and not enough to do anything with the finding - there is no
way to sort turns by worst score, compare two phrasings of the same question,
or see how much of the turn the embedding call ate. This sends the same
decision to LangSmith as a structured trace, where those are queries rather
than grep.

The trace is a `retriever` run with two children, which is the breakdown that
matters here: the query embedding (the hosted provider measured at 0.28-0.43s
warm and 4-12s cold, against CLAUDE.md's 80ms budget for the whole of
retrieval) and the pgvector search (single-digit milliseconds). Knowing which
of the two a slow turn spent its time in is the first question, and until now
neither was timed separately.

**What is deliberately not sent.** CLAUDE.md section 27 forbids logging
transcript text, caller PII and full document contents, and a trace shipped
to a third-party service is a stronger form of exactly that. So by default
the caller's question and the chunk text are withheld: what goes out is
scores, identifiers, lengths, counts and timings - the same shape 25a's log
line already takes. `LANGSMITH_TRACE_TEXT=true` opts in to sending both, for
debugging a corpus you own with callers who are you; it is off by default and
should stay off anywhere real calls land.

Tracing is on exactly when `LANGSMITH_API_KEY` is set. Nothing here may ever
raise or block: retrieval sits inside the turn's latency budget, and an
observability backend having a bad day must not cost a caller their answer.
Every entry point swallows its own failures and the trace is simply missing.
"""

import logging
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from norma_shared.correlation import current_call_context

from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

# Stands in for the caller's question when text is not being sent. A fixed
# string rather than an omitted field, so a trace with no query reads as
# "withheld on purpose" rather than "something went wrong upstream".
WITHHELD = "[withheld: LANGSMITH_TRACE_TEXT is off]"

_client: Any = None
_client_failed = False


class _Recorder(Protocol):
    def record(
        self,
        chunks: Sequence["RetrievedChunk"],
        kept_ids: set[uuid.UUID],
    ) -> None: ...


class _NullRecorder:
    """
    What every caller gets when tracing is off, or when anything at all went
    wrong setting it up. Retrieval's own code path is then identical to what
    it was before this module existed.
    """

    def record(
        self,
        chunks: Sequence["RetrievedChunk"],
        kept_ids: set[uuid.UUID],
    ) -> None:
        return


class _RunRecorder:
    def __init__(self, run: Any, *, include_text: bool) -> None:
        self._run = run
        self._include_text = include_text

    def record(
        self,
        chunks: Sequence["RetrievedChunk"],
        kept_ids: set[uuid.UUID],
    ) -> None:
        try:
            self._run.end(outputs={"documents": documents_of(chunks, kept_ids)})
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "could not attach retrieval outputs to the trace", exc_info=True
            )


def tracing_enabled() -> bool:
    return bool(settings.langsmith_api_key)


def documents_of(
    chunks: Sequence["RetrievedChunk"], kept_ids: set[uuid.UUID]
) -> list[dict[str, Any]]:
    """
    The retrieved chunks in LangSmith's own document shape, so its retriever
    view renders them as a ranked list rather than as an opaque blob.

    `used` is the field worth having here and absent from most retrieval
    traces: retrieval returns more than the context builder's character
    budget can carry, so a chunk can be retrieved, scored well, and still
    never reach the model. A trace that does not distinguish the two sends
    someone looking for a bug in the ranking.
    """

    return [
        {
            "page_content": (
                chunk.text if _include_text() else f"[{len(chunk.text)} chars withheld]"
            ),
            "metadata": {
                "chunk_id": str(chunk.chunk_id),
                "knowledge_source_id": str(chunk.knowledge_source_id),
                "source_type": chunk.source_type,
                "score": round(chunk.score, 4),
                "chars": len(chunk.text),
                "used": chunk.chunk_id in kept_ids,
            },
        }
        for chunk in chunks
    ]


def _include_text() -> bool:
    return settings.langsmith_trace_text


def _get_client() -> Any:
    """
    One LangSmith client for the process, built on first use.

    It owns a connection pool and a background thread that batches runs out
    of the request path, so building one per retrieval would put the cost
    this module exists to measure back into the turn. A construction failure
    is remembered, so a missing package or a malformed endpoint is not
    retried on every single turn.
    """

    global _client, _client_failed

    if _client is not None or _client_failed:
        return _client

    try:
        from langsmith import Client

        _client = Client(
            api_key=settings.langsmith_api_key,
            api_url=settings.langsmith_endpoint or None,
        )
    except Exception:
        _client_failed = True
        logger.warning(
            "LangSmith tracing is configured but the client could not be built - "
            "retrieval will run untraced",
            exc_info=True,
        )

    return _client


@contextmanager
def trace_retrieval(
    *,
    assistant_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    query: str,
    top_k: int,
    min_score: float,
) -> Iterator[_Recorder]:
    """
    Open a trace for one turn's retrieval, yielding something to hand the
    results to.

    Correlation identifiers go on as metadata, which is the point of putting
    them there rather than only in the log: a LangSmith trace and the media
    plane's log lines for the same turn are otherwise two accounts of an
    event with nothing tying them together (item 25b).
    """

    if not tracing_enabled():
        yield _NullRecorder()

        return

    client = _get_client()

    if client is None:
        yield _NullRecorder()

        return

    try:
        from langsmith.run_helpers import trace, tracing_context
    except Exception:  # pragma: no cover - defensive
        logger.warning("LangSmith is configured but not importable", exc_info=True)
        yield _NullRecorder()

        return

    context = current_call_context()
    metadata: dict[str, Any] = {
        "assistant_id": str(assistant_id),
        "organization_id": str(organization_id),
        "workspace_id": str(workspace_id),
        "embedding_model": settings.embedding_model,
        "embedding_provider": settings.embedding_provider,
        "top_k": top_k,
        "min_score": min_score,
        "text_recorded": _include_text(),
    }

    if context is not None:
        metadata["call_id"] = str(context.call_id)

        if context.turn_id is not None:
            metadata["turn_id"] = str(context.turn_id)

    try:
        # enabled=True explicitly: the SDK otherwise decides from
        # LANGSMITH_TRACING, and this project's switch is the presence of a
        # key, so that a pasted key is all it takes.
        with tracing_context(
            enabled=True, client=client, project_name=settings.langsmith_project
        ):
            with trace(
                "retrieval",
                run_type="retriever",
                inputs={
                    "query": query if _include_text() else WITHHELD,
                    "top_k": top_k,
                    "min_score": min_score,
                },
                metadata=metadata,
                tags=["retrieval", f"assistant:{assistant_id}"],
            ) as run:
                yield _RunRecorder(run, include_text=_include_text())
    except Exception:
        # Reached only if the SDK itself fails - a retrieval error propagates
        # through the `with` above and is recorded on the run first. Either
        # way the caller's answer matters more than the trace.
        logger.warning("retrieval tracing failed", exc_info=True)

        yield _NullRecorder()


# LangSmith validates run_type server-side and rejects the entire ingest
# batch a bad value arrives in - so one mistyped span takes its siblings, the
# root run included, down with it, and the only evidence is a 422 in the
# client's own warning log. Checked here instead, where the span is simply
# dropped and the rest of the trace survives.
VALID_RUN_TYPES = frozenset(
    {"tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"}
)


@contextmanager
def trace_step(name: str, run_type: str, **metadata: Any) -> Iterator[None]:
    """
    A child span inside an open retrieval trace - the embedding call and the
    vector search.

    Does nothing unless a trace is already open, which is deliberate: the
    same embedding provider is called by background FAQ generation and by
    cache warming, and those would otherwise each start a stray root trace
    with no retrieval around them.
    """

    if run_type not in VALID_RUN_TYPES:
        logger.warning(
            "refusing to trace %s: %r is not a LangSmith run type", name, run_type
        )

        yield

        return

    if not tracing_enabled():
        yield

        return

    try:
        from langsmith.run_helpers import get_current_run_tree, trace
    except Exception:  # pragma: no cover - defensive
        yield

        return

    if get_current_run_tree() is None:
        yield

        return

    try:
        with trace(name, run_type=run_type, metadata=metadata):
            yield
    except Exception:
        logger.debug("could not record the %s span", name, exc_info=True)

        yield


def flush() -> None:
    """
    Push anything still batched before the process exits.

    The client sends runs from a background thread, so without this a
    shutdown loses whatever was queued - which in development is most of
    what just happened, since that is when someone restarts the API to look
    at a trace.
    """

    if _client is None:
        return

    try:
        _client.flush()
    except Exception:  # pragma: no cover - defensive
        logger.debug("could not flush LangSmith runs on shutdown", exc_info=True)
