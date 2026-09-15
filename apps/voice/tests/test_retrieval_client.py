import uuid

import httpx
import pytest
from norma_shared.correlation import (
    CALL_ID_HEADER,
    TURN_ID_HEADER,
    CallContext,
    bind_call_context,
    unbind_call_context,
)

from app import config
from app.retrieval_client import fetch_retrieved_context, warm_retrieval_cache

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


async def test_warming_calls_the_warm_endpoint_with_the_internal_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"warmed": 24})

    await warm_retrieval_cache(_ASSISTANT_ID, client=_client_returning(handler))

    assert len(seen) == 1
    assert seen[0].url.path.endswith(f"/{_ASSISTANT_ID}/retrieve/warm")
    assert seen[0].headers["X-Internal-Secret"] == "the-real-secret"


async def test_warming_waits_longer_than_a_turn_would() -> None:
    """
    Nothing is waiting on this - it runs alongside the other session-start
    fetches, while the greeting plays - so it can afford the hosted
    provider's slow tail in a way the per-turn path cannot.
    """

    seen: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"warmed": 0})

    await warm_retrieval_cache(_ASSISTANT_ID, client=_client_returning(handler))

    assert seen[0] is not None
    assert seen[0] >= 10.0


async def test_warming_never_raises_so_a_session_can_always_start() -> None:
    """
    Warming is an optimisation. A session that could not start because the
    optimisation failed would be a far worse bug than the latency it exists
    to remove.
    """

    for failure in (
        lambda _r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        lambda _r: (_ for _ in ()).throw(httpx.ReadTimeout("timed out")),
        lambda _r: httpx.Response(500),
        lambda _r: httpx.Response(200, content=b"not json"),
    ):
        await warm_retrieval_cache(_ASSISTANT_ID, client=_client_returning(failure))


async def test_a_failed_warm_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with caplog.at_level(logging.WARNING):
        await warm_retrieval_cache(_ASSISTANT_ID, client=_client_returning(handler))

    assert any("503" in record.getMessage() for record in caplog.records)


async def test_warming_logs_a_count_but_never_a_question(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    CLAUDE.md section 27 / item 24d: FAQ questions are knowledge content,
    and the count is what makes the warm observable without logging any of
    it.
    """

    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"warmed": 24})

    with caplog.at_level(logging.INFO):
        await warm_retrieval_cache(_ASSISTANT_ID, client=_client_returning(handler))

    logged = "|".join(record.getMessage() for record in caplog.records)

    assert "24" in logged
    assert str(_ASSISTANT_ID) in logged


async def test_the_turns_retrieval_request_names_the_call_and_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 25b. This is the request that most needs correlating: retrieval
    happens on every turn, and CLAUDE.md section 27's stated reason for
    correlation IDs is that a latency problem across the two planes is
    otherwise undebuggable. The API's own line about this retrieval has to
    carry the same call and turn the media plane's does.
    """

    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    call_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    token = bind_call_context(CallContext(call_id=call_id, turn_id=turn_id))
    sent_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_headers.update(request.headers)

        return httpx.Response(200, json={"context": ""})

    try:
        await fetch_retrieved_context(
            _ASSISTANT_ID, "What are your hours?", client=_client_returning(handler)
        )
    finally:
        unbind_call_context(token)

    assert sent_headers[CALL_ID_HEADER.lower()] == str(call_id)
    assert sent_headers[TURN_ID_HEADER.lower()] == str(turn_id)
    # Still authenticated - correlation is added alongside the secret, not
    # instead of it.
    assert sent_headers["x-internal-secret"] == "the-real-secret"


async def test_a_request_made_outside_a_call_carries_no_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Cache warming runs before any turn exists, and a background job runs
    outside a call entirely. Neither should invent identifiers.
    """

    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    sent_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_headers.update(request.headers)

        return httpx.Response(200, json={"warmed": 0})

    await warm_retrieval_cache(_ASSISTANT_ID, client=_client_returning(handler))

    assert CALL_ID_HEADER.lower() not in sent_headers
    assert TURN_ID_HEADER.lower() not in sent_headers


async def test_a_timeout_is_marked_as_a_failed_lookup() -> None:
    """
    The regression for a contradiction a caller heard on one call.

    "What is cursor agent" was answered in full; "Tell me, what is cursor
    agent" was refused seconds later with "I don't have that detail to hand".
    Both phrasings retrieve the same five chunks when measured (0.813 and
    0.797) - the second turn's lookup had simply timed out.

    The refusal was therefore a falsehood, and the prompt could not know: an
    empty string meant both "searched, covers nothing" and "never searched".
    It has to carry which.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "what is cursor agent", client=_client_returning(handler)
    )

    assert context == ""
    assert context.lookup_failed is True


async def test_a_search_that_matched_nothing_is_not_a_failed_lookup() -> None:
    """
    The other half, and the one that must not regress into asking the caller
    to repeat a question nothing can answer: retrieval ran, matched nothing,
    and "I don't have that detail" is the true answer.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"context": ""})

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "who won the cricket", client=_client_returning(handler)
    )

    assert context == ""
    assert context.lookup_failed is False


async def test_every_other_failure_is_also_a_failed_lookup() -> None:
    """One flag, set on every path where the answer was never looked for."""

    cases = {
        "non-200": lambda request: httpx.Response(503),
        "malformed body": lambda request: httpx.Response(200, content=b"not json"),
        "no context field": lambda request: httpx.Response(200, json={"other": 1}),
    }

    for name, handler in cases.items():
        context = await fetch_retrieved_context(
            _ASSISTANT_ID, "anything", client=_client_returning(handler)
        )

        assert context == "", name
        assert context.lookup_failed is True, name
