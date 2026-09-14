import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import Assistant
from app.models.chunk import Chunk
from app.models.faq_entry import FaqEntry
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.providers.mock_embedding import MockEmbeddingProvider
from app.services.query_embedding_cache import clear_query_embedding_cache

_RETRIEVE_URL = "/internal/v1/assistants/{assistant_id}/retrieve"
_WARM_URL = "/internal/v1/assistants/{assistant_id}/retrieve/warm"


async def _make_assistant(db: AsyncSession, slug: str) -> Assistant:
    organization = Organization(name=slug, slug=slug)
    db.add(organization)
    await db.flush()

    workspace = Workspace(organization_id=organization.id, name="Clinic")
    db.add(workspace)
    await db.flush()

    assistant = Assistant(
        organization_id=organization.id,
        workspace_id=workspace.id,
        name="Test Assistant",
    )
    db.add(assistant)
    await db.flush()

    return assistant, organization, workspace


async def test_returns_a_matching_chunk_in_the_context_string(
    client: AsyncClient, db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    assistant, organization, workspace = await _make_assistant(
        db, "internal-retrieve-ok"
    )
    source = KnowledgeSource(
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        type="file",
    )
    db.add(source)
    await db.flush()

    query = "What are your business hours?"
    [vector] = await embedding_provider.embed([query])
    db.add(
        Chunk(
            organization_id=organization.id,
            workspace_id=workspace.id,
            assistant_id=assistant.id,
            knowledge_source_id=source.id,
            text=query,
            ordering=0,
            chunk_metadata={},
            embedding=vector,
        )
    )
    await db.flush()

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": query},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert query in response.json()["context"]


async def test_returns_empty_context_when_nothing_matches(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-retrieve-empty"
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": "anything"},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["context"] == ""
    # Nothing retrieved, said explicitly rather than by an empty string -
    # "the knowledge does not cover this" and "retrieval returned chunks the
    # context builder then dropped" are different problems.
    assert body["retrieved"] == []


async def test_404s_for_an_unknown_assistant(client: AsyncClient) -> None:
    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=uuid.uuid4()),
        json={"query": "anything"},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 404


async def test_422s_for_an_empty_query(client: AsyncClient, db: AsyncSession) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-retrieve-invalid"
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": ""},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 422


async def test_401s_with_a_missing_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-retrieve-no-header"
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id), json={"query": "anything"}
    )

    assert response.status_code == 401


async def _add_faq(
    db: AsyncSession, assistant, organization, workspace, questions: list[str]
) -> None:
    source = KnowledgeSource(
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        type="manual_faq",
    )
    db.add(source)
    await db.flush()

    for question in questions:
        db.add(
            FaqEntry(
                knowledge_source_id=source.id,
                question=question,
                answer="An answer.",
            )
        )

    await db.flush()


async def test_warming_embeds_the_assistant_s_faq_questions(
    client: AsyncClient, db: AsyncSession
) -> None:
    """
    The hosted embedding router is bimodal - mostly 0.3s, roughly one call
    in three taking 4-12s - and the media plane will not wait that long
    inside a turn. Warming spends that time before the conversation starts
    instead, on exactly the phrasings callers use.
    """

    clear_query_embedding_cache()

    assistant, organization, workspace = await _make_assistant(db, "internal-warm")
    await _add_faq(
        db,
        assistant,
        organization,
        workspace,
        ["What are your hours?", "Where are you located?"],
    )

    response = await client.post(
        _WARM_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert response.json() == {"warmed": 2}


async def test_warming_twice_does_not_re_embed(
    client: AsyncClient, db: AsyncSession
) -> None:
    """
    Two browser test calls in a row must not each pay the provider again.
    """

    clear_query_embedding_cache()

    assistant, organization, workspace = await _make_assistant(db, "internal-warm-idem")
    await _add_faq(db, assistant, organization, workspace, ["What are your hours?"])

    headers = {"X-Internal-Secret": settings.internal_api_secret}
    url = _WARM_URL.format(assistant_id=assistant.id)

    assert (await client.post(url, headers=headers)).json() == {"warmed": 1}
    assert (await client.post(url, headers=headers)).json() == {"warmed": 0}


async def test_warming_an_assistant_with_no_faqs_still_wakes_the_provider(
    client: AsyncClient,
    db: AsyncSession,
    embedding_provider: MockEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    There is nothing to cache, and that is not the same as nothing to do.

    This used to be a genuine no-op: no FAQ questions, so no queries to
    embed, so no call to the provider at all. An assistant with no FAQs is
    precisely the one whose every caller question will be an uncached miss,
    so it is the one that most needs the provider awake - and on a live
    call the warm reported "entries=0" and was read as "nothing to do".

    The hosted router goes cold when idle. Measured straight after an idle
    period, the first call runs 3.1-7.0s against a 1.5s per-turn retrieval
    budget, so the first turn is abandoned - and an abandoned call never
    completes the wake-up, so the next turn pays it again. Eight consecutive
    turns each lost their knowledge that way. One patient call at session
    start, while the greeting is playing, and the turns after it ran
    479-673ms with none lost.
    """

    clear_query_embedding_cache()

    embedded: list[list[str]] = []
    real_embed = embedding_provider.embed

    async def recording_embed(texts: list[str]) -> list[list[float]]:
        embedded.append(list(texts))

        return await real_embed(texts)

    monkeypatch.setattr(embedding_provider, "embed", recording_embed)

    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-warm-empty"
    )

    response = await client.post(
        _WARM_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    # Still nothing cached - there were no questions to cache.
    assert response.json() == {"warmed": 0}
    # But the provider was reached, which is the whole point.
    assert len(embedded) == 1, "the provider was never called, so it stays cold"


async def test_warming_does_not_reach_another_assistant_s_questions(
    client: AsyncClient, db: AsyncSession
) -> None:
    """
    Knowledge is scoped per assistant (feature 23d). Warming is a read of
    that same knowledge and must respect the same boundary - a cache keyed
    only on text is shared process-wide, so warming the wrong assistant's
    questions would be one tenant paying to speed up another's calls.
    """

    clear_query_embedding_cache()

    mine, my_org, my_workspace = await _make_assistant(db, "internal-warm-mine")
    theirs, their_org, their_workspace = await _make_assistant(
        db, "internal-warm-theirs"
    )

    await _add_faq(db, mine, my_org, my_workspace, ["Do you take walk-ins?"])
    await _add_faq(
        db, theirs, their_org, their_workspace, ["What is your cancellation policy?"]
    )

    response = await client.post(
        _WARM_URL.format(assistant_id=mine.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.json() == {"warmed": 1}


async def test_warming_404s_for_an_unknown_assistant(client: AsyncClient) -> None:
    response = await client.post(
        _WARM_URL.format(assistant_id=uuid.uuid4()),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 404


async def test_warming_401s_with_a_missing_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-warm-no-header"
    )

    response = await client.post(_WARM_URL.format(assistant_id=assistant.id))

    assert response.status_code == 401
