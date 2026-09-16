"""
Item 25a: retrieval traced to LangSmith.

Two things matter more than the happy path here, and both are what these
cover. First, that nothing in this layer can hurt a call: retrieval sits
inside the turn's latency budget, so a misconfigured key, an SDK that
raises, or an observability backend having a bad day must leave the answer
untouched. Second, that the caller's question and the operator's documents
do not leave the building by default - a trace posted to a third-party
service is the strongest form of the logging CLAUDE.md section 27 forbids.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import retrieval_tracing
from app.services.retrieval import RetrievedChunk
from app.services.retrieval_tracing import (
    WITHHELD,
    documents_of,
    trace_retrieval,
    trace_step,
    tracing_enabled,
)

_RETRIEVE_URL = "/internal/v1/assistants/{assistant_id}/retrieve"


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """
    The client is a process-global built once. A test that induces a
    construction failure would otherwise poison every test after it.
    """

    retrieval_tracing._client = None
    retrieval_tracing._client_failed = False

    yield

    retrieval_tracing._client = None
    retrieval_tracing._client_failed = False


def _chunk(text: str = "We open at nine.", score: float = 0.81) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        knowledge_source_id=uuid.uuid4(),
        source_type="website",
        text=text,
        metadata={},
        score=score,
    )


def test_tracing_is_off_until_a_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "langsmith_api_key", "")

    assert tracing_enabled() is False

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")

    assert tracing_enabled() is True


class _RecordingTrace:
    """
    Stands in for langsmith's own `trace`, capturing what would have been
    sent without a network call. Only the surface this module uses.
    """

    calls: list[dict] = []

    def __init__(self, name, run_type="chain", **kwargs) -> None:
        type(self).calls.append({"name": name, "run_type": run_type, **kwargs})

    def __enter__(self):
        class _Run:
            def end(self, outputs=None):
                _RecordingTrace.calls[-1]["outputs"] = outputs

        return _Run()

    def __exit__(self, *_exc) -> None:
        return None


@pytest.fixture
def recorded_trace(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingTrace]:
    """
    Tracing enabled, with the SDK's network replaced by a recorder.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")
    monkeypatch.setattr(retrieval_tracing, "_get_client", lambda: object())
    monkeypatch.setattr("langsmith.run_helpers.trace", _RecordingTrace)
    _RecordingTrace.calls = []

    return _RecordingTrace


def test_the_question_is_withheld_by_default(
    monkeypatch: pytest.MonkeyPatch, recorded_trace: type[_RecordingTrace]
) -> None:
    """
    The query is whatever the caller just said. Section 27 forbids logging
    transcript text, and sending it to a third party is worse, not equal.
    """

    monkeypatch.setattr(settings, "langsmith_trace_text", False)

    with trace_retrieval(
        assistant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        query="my card number is 4111 1111 1111 1111",
        top_k=5,
        min_score=0.62,
    ) as traced:
        traced.record([_chunk()], set())

    [sent] = recorded_trace.calls

    assert sent["inputs"]["query"] == WITHHELD
    assert "4111" not in str(sent)


def test_the_question_is_sent_when_opted_into(
    monkeypatch: pytest.MonkeyPatch, recorded_trace: type[_RecordingTrace]
) -> None:
    monkeypatch.setattr(settings, "langsmith_trace_text", True)

    with trace_retrieval(
        assistant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        query="what are your hours?",
        top_k=5,
        min_score=0.62,
    ) as traced:
        traced.record([], set())

    [sent] = recorded_trace.calls

    assert sent["inputs"]["query"] == "what are your hours?"


def test_the_trace_names_the_call_and_turn_it_belongs_to(
    monkeypatch: pytest.MonkeyPatch, recorded_trace: type[_RecordingTrace]
) -> None:
    """
    Item 25b's identifiers, carried onto the trace. Without them a LangSmith
    run and the media plane's log lines for the same turn are two accounts
    of one event with nothing tying them together.
    """

    from norma_shared.correlation import (
        CallContext,
        bind_call_context,
        unbind_call_context,
    )

    monkeypatch.setattr(settings, "langsmith_trace_text", False)
    call_id, turn_id = uuid.uuid4(), uuid.uuid4()
    token = bind_call_context(CallContext(call_id=call_id, turn_id=turn_id))

    try:
        with trace_retrieval(
            assistant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            query="anything",
            top_k=5,
            min_score=0.62,
        ) as traced:
            traced.record([], set())
    finally:
        unbind_call_context(token)

    [sent] = recorded_trace.calls

    assert sent["metadata"]["call_id"] == str(call_id)
    assert sent["metadata"]["turn_id"] == str(turn_id)
    assert sent["metadata"]["text_recorded"] is False


def test_a_retrieval_outside_a_call_still_traces(
    monkeypatch: pytest.MonkeyPatch, recorded_trace: type[_RecordingTrace]
) -> None:
    """
    A retrieval triggered from a script or a test has no call around it. It
    should still be traceable, just without identifiers it does not have.
    """

    monkeypatch.setattr(settings, "langsmith_trace_text", False)

    with trace_retrieval(
        assistant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        query="anything",
        top_k=5,
        min_score=0.62,
    ) as traced:
        traced.record([], set())

    [sent] = recorded_trace.calls

    assert "call_id" not in sent["metadata"]


def test_the_scores_reach_the_trace_as_ranked_documents(
    monkeypatch: pytest.MonkeyPatch, recorded_trace: type[_RecordingTrace]
) -> None:
    monkeypatch.setattr(settings, "langsmith_trace_text", False)

    best, worst = _chunk(score=0.91), _chunk(score=0.63)

    with trace_retrieval(
        assistant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        query="anything",
        top_k=5,
        min_score=0.62,
    ) as traced:
        traced.record([best, worst], {best.chunk_id})

    [sent] = recorded_trace.calls
    documents = sent["outputs"]["documents"]

    assert [d["metadata"]["score"] for d in documents] == [0.91, 0.63]
    assert [d["metadata"]["used"] for d in documents] == [True, False]


def test_chunk_text_is_withheld_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langsmith_trace_text", False)

    chunk = _chunk(text="Our late-night rate is 90 dollars.")

    [document] = documents_of([chunk], {chunk.chunk_id})

    assert "90 dollars" not in document["page_content"]
    assert document["page_content"] == "[34 chars withheld]"
    assert document["metadata"]["chars"] == 34


def test_text_is_sent_when_it_is_explicitly_opted_into(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The escape hatch for debugging a corpus you own. It exists because
    scores alone cannot answer "is this chunk even about the question", and
    it is off by default for the reason above.
    """

    monkeypatch.setattr(settings, "langsmith_trace_text", True)

    chunk = _chunk(text="Our late-night rate is 90 dollars.")

    [document] = documents_of([chunk], {chunk.chunk_id})

    assert document["page_content"] == "Our late-night rate is 90 dollars."


def test_a_document_says_whether_it_actually_reached_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Retrieval returns more than the context builder's character budget can
    carry, so a chunk can score well, be retrieved, and still never be seen
    by the model. A trace that cannot tell those apart sends someone hunting
    for a ranking bug that is not there.
    """

    monkeypatch.setattr(settings, "langsmith_trace_text", False)

    used, dropped = _chunk(score=0.9), _chunk(score=0.88)

    documents = documents_of([used, dropped], {used.chunk_id})

    assert documents[0]["metadata"]["used"] is True
    assert documents[1]["metadata"]["used"] is False


def test_a_span_with_an_invalid_run_type_is_dropped_not_sent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Regression. "embedder" is not a LangSmith run type ("embedding" is), and
    the server validates it on ingest by rejecting the *entire batch* the
    bad run arrived in - so one mistyped span silently took the root
    retrieval run and its sibling down with it, leaving nothing but a 422 in
    the SDK's own warning log. Caught here instead, where only that span is
    lost.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")

    with caplog.at_level("WARNING"):
        with trace_step("embed_query", "embedder", model="x"):
            pass

    assert "not a LangSmith run type" in caplog.text
    assert "embedding" in retrieval_tracing.VALID_RUN_TYPES
    assert "embedder" not in retrieval_tracing.VALID_RUN_TYPES


def test_a_span_outside_any_trace_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The same embedding provider is called by FAQ generation and by cache
    warming. Neither should start a stray root trace that looks like a turn.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")

    with trace_step("embed_query", "embedding", model="x"):
        pass


def test_a_client_that_cannot_be_built_leaves_retrieval_untouched(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")

    def _explode(*_args, **_kwargs):
        raise RuntimeError("no network, no key, no anything")

    monkeypatch.setattr("langsmith.Client", _explode)

    with caplog.at_level("WARNING"):
        with trace_retrieval(
            assistant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            query="anything",
            top_k=5,
            min_score=0.62,
        ) as traced:
            traced.record([_chunk()], set())

    assert "could not be built" in caplog.text


def test_a_failing_client_is_not_rebuilt_on_every_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A construction failure is remembered. Retrying it per turn would put a
    guaranteed-failing call inside the latency budget, on every turn, for as
    long as the misconfiguration lasts.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")
    attempts = []

    def _explode(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("nope")

    monkeypatch.setattr("langsmith.Client", _explode)

    for _ in range(3):
        retrieval_tracing._get_client()

    assert len(attempts) == 1


def test_an_sdk_that_raises_does_not_break_the_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    The whole point of this layer being optional. Whatever LangSmith does,
    the caller still gets their answer.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")
    monkeypatch.setattr(retrieval_tracing, "_get_client", lambda: object())

    def _explode(*_args, **_kwargs):
        raise RuntimeError("langsmith fell over")

    monkeypatch.setattr("langsmith.run_helpers.trace", _explode)

    reached = False

    with caplog.at_level("WARNING"):
        with trace_retrieval(
            assistant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            query="anything",
            top_k=5,
            min_score=0.62,
        ) as traced:
            reached = True
            traced.record([_chunk()], set())

    assert reached
    assert "retrieval tracing failed" in caplog.text


def test_flush_is_safe_before_any_client_exists() -> None:
    retrieval_tracing.flush()


async def test_the_retrieve_endpoint_is_unchanged_when_tracing_cannot_work(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The end-to-end guarantee: a broken observability backend costs a caller
    nothing. Same status, same body, whether tracing is off or configured
    and failing.
    """

    from app.models.assistant import Assistant
    from app.models.organization import Organization
    from app.models.workspace import Workspace

    organization = Organization(name="tracing", slug="tracing-safety")
    db.add(organization)
    await db.flush()
    workspace = Workspace(organization_id=organization.id, name="Clinic")
    db.add(workspace)
    await db.flush()
    assistant = Assistant(
        organization_id=organization.id,
        workspace_id=workspace.id,
        name="Traced Assistant",
    )
    db.add(assistant)
    await db.flush()

    url = _RETRIEVE_URL.format(assistant_id=assistant.id)
    headers = {"X-Internal-Secret": settings.internal_api_secret}

    monkeypatch.setattr(settings, "langsmith_api_key", "")
    untraced = await client.post(url, json={"query": "hours?"}, headers=headers)

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_pt_example")

    def _explode(*_args, **_kwargs):
        raise RuntimeError("langsmith is down")

    monkeypatch.setattr("langsmith.Client", _explode)

    traced = await client.post(url, json={"query": "hours?"}, headers=headers)

    assert untraced.status_code == traced.status_code == 200
    assert untraced.json() == traced.json()


def test_an_error_inside_a_traced_step_reaches_the_caller_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The regression for a live outage made unreadable by its own telemetry.

    Both helpers wrapped their own yield in a try/except, on the stated
    assumption that an error in the traced body "propagates through the with
    above". It does not. A @contextmanager generator is suspended at its
    yield, so the caller's exception is thrown *into* the generator there -
    the except caught it, swallowed it, and yielded a second time, and
    contextlib turned that into "generator didn't stop after throw()".

    The day HuggingFace's inference API returned 500s, a clean
    EmbeddingProviderUnavailable became an opaque RuntimeError, the endpoint
    answered 500 instead of degrading, and the traceback named this module
    instead of the provider that was down.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "test-key")

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with retrieval_tracing.trace_step("embed_query", "embedding"):
            raise _Boom("the provider is down")


def test_an_error_inside_a_traced_retrieval_reaches_the_caller_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same hole in the outer helper, which is where it was hit."""

    monkeypatch.setattr(settings, "langsmith_api_key", "test-key")

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with retrieval_tracing.trace_retrieval(
            assistant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            query="anything",
            top_k=5,
            min_score=0.6,
        ):
            raise _Boom("the provider is down")


def test_tracing_still_does_not_raise_on_its_own_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The other half of the rule, and what the swallowing was there for: if the
    tracing SDK itself fails, the caller must not notice. Opening and closing
    a span are guarded; only the body between them is not.
    """

    monkeypatch.setattr(settings, "langsmith_api_key", "test-key")
    # How a broken SDK surfaces: the span never opens.
    monkeypatch.setattr(
        retrieval_tracing,
        "_open_span",
        lambda factory, name, **_kwargs: (None, None),
    )

    with retrieval_tracing.trace_step("embed_query", "embedding"):
        pass

    with retrieval_tracing.trace_retrieval(
        assistant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        query="anything",
        top_k=5,
        min_score=0.6,
    ) as recorder:
        assert recorder is not None
