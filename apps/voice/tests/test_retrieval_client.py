import uuid

import httpx
import pytest

from app import config
from app.retrieval_client import fetch_retrieved_context

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_returns_the_context_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)
        assert request.content == b'{"query":"What are your hours?"}'

        return httpx.Response(200, json={"context": "We close at 5pm."})

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "What are your hours?", client=_client_returning(handler)
    )

    assert context == "We close at 5pm."


async def test_returns_empty_string_on_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert context == ""


async def test_returns_empty_string_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert context == ""


async def test_returns_empty_string_for_a_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"context": None})

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert context == ""


async def test_returns_empty_string_on_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    The regression this covers: reported live as "the assistant only answers
    the auto-generated FAQs, nothing else in the document" - traced to
    retrieval silently timing out under the real embedding provider's
    latency, indistinguishable from the document genuinely lacking the
    answer. The caller-facing fallback must stay unchanged; what's new is
    that this is now logged.
    """

    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with caplog.at_level(logging.WARNING):
        context = await fetch_retrieved_context(
            _ASSISTANT_ID, "anything", client=_client_returning(handler)
        )

    assert context == ""
    assert any("timed out" in record.getMessage() for record in caplog.records)
    assert any(str(_ASSISTANT_ID) in record.getMessage() for record in caplog.records)


async def test_returns_empty_string_for_invalid_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    response.json() raises json.JSONDecodeError (a ValueError, not an
    httpx.HTTPError) on a malformed but 200 body - this must still fail
    open, not propagate.
    """

    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    with caplog.at_level(logging.WARNING):
        context = await fetch_retrieved_context(
            _ASSISTANT_ID, "anything", client=_client_returning(handler)
        )

    assert context == ""
    assert any("not valid JSON" in record.getMessage() for record in caplog.records)


async def test_a_non_200_response_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with caplog.at_level(logging.WARNING):
        await fetch_retrieved_context(
            _ASSISTANT_ID, "anything", client=_client_returning(handler)
        )

    assert any("404" in record.getMessage() for record in caplog.records)


async def test_a_connection_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with caplog.at_level(logging.WARNING):
        await fetch_retrieved_context(
            _ASSISTANT_ID, "anything", client=_client_returning(handler)
        )

    assert any("ConnectError" in record.getMessage() for record in caplog.records)


async def test_the_caller_s_query_text_never_reaches_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    CLAUDE.md section 27 / item 24d: the failure logs added here name the
    assistant and the failure type, never the caller's own words.
    """

    import logging

    caller_text = "My name is Wilhelmina Bracegirdle and I need the Quaxton file."

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with caplog.at_level(logging.WARNING):
        await fetch_retrieved_context(
            _ASSISTANT_ID, caller_text, client=_client_returning(handler)
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert "Wilhelmina" not in logged
    assert "Quaxton" not in logged


async def test_the_request_carries_a_timeout_short_enough_to_answer_without_it() -> (
    None
):
    """
    The regression behind "sometimes it is not responding anything": with a
    five-second budget here, a slow retrieval did not merely answer late.
    The caller heard nothing, concluded the assistant had not understood,
    and spoke again - and that second utterance barged in on and cancelled
    their own pending turn. Seven of thirty-five turns recorded stt
    finalized, no retrieval, no LLM token, no audio.

    CLAUDE.md's own numbers are what this has to fit inside: p95 time to
    first audio is 1200ms, and retrieval is budgeted 80ms of it. Retrieval
    cannot meet 80ms against a hosted embedding provider today, but it must
    at least stay short enough that the turn proceeds - answering without
    knowledge - while the caller is still waiting rather than after they
    have given up.
    """

    seen: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"context": ""})

    await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert seen and seen[0] is not None
    assert seen[0] <= 2.0


async def test_the_timeout_is_configurable() -> None:
    """
    The right value follows the embedding provider - hosting the model
    locally, or caching query embeddings, changes what is affordable - so it
    must not need a code change.
    """

    import importlib
    import os

    import app.retrieval_client as module

    os.environ["RETRIEVAL_TIMEOUT_SECONDS"] = "0.25"
    try:
        importlib.reload(module)
        assert module._TIMEOUT_SECONDS == 0.25
    finally:
        del os.environ["RETRIEVAL_TIMEOUT_SECONDS"]
        importlib.reload(module)

    assert module._TIMEOUT_SECONDS == 1.5
