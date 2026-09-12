import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import Assistant
from app.models.organization import Organization
from app.models.turn_metric import TurnMetric
from app.models.workspace import Workspace

_TURN_METRICS_URL = "/internal/v1/assistants/{assistant_id}/turn-metrics"


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

    return assistant


async def test_persists_a_row_and_returns_its_id(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-turn-metrics-ok")
    call_id = uuid.uuid4()
    now = datetime.now(UTC).isoformat()

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={"call_id": str(call_id), "stt_finalized_at": now, "audio_out_at": now},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    row_id = uuid.UUID(response.json()["id"])

    row = await db.scalar(select(TurnMetric).where(TurnMetric.id == row_id))
    assert row is not None
    assert row.call_id == call_id
    assert row.assistant_id == assistant.id


async def test_404s_for_an_unknown_assistant(client: AsyncClient) -> None:
    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=uuid.uuid4()),
        json={"call_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 404


async def test_401s_with_a_missing_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-turn-metrics-no-header")

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={"call_id": str(uuid.uuid4())},
    )

    assert response.status_code == 401


async def test_401s_with_a_wrong_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-turn-metrics-wrong-header")

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={"call_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": "definitely-not-the-real-secret"},
    )

    assert response.status_code == 401


async def test_the_route_commits_so_the_metric_outlives_the_request(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The route flushed but never committed. Flushing assigns an id, so the
    caller got a 200 with an id back while every row was rolled back when the
    request ended - the table stayed empty and per-turn latency could not be
    measured at all.

    This asserts the commit happens rather than reading the row back, because
    reading it back cannot fail here: every test runs inside one transaction
    that is rolled back afterwards (see conftest), so a flushed-but-
    uncommitted row is visible to the test session exactly like a committed
    one. That is precisely why the suite did not catch this, and why the check
    has to be on the call.
    """

    assistant = await _make_assistant(db, "turn-metric-commits")
    await db.flush()

    commits: list[int] = []
    original = AsyncSession.commit

    async def _counting_commit(self):
        commits.append(1)
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", _counting_commit)

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={"call_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert commits, "the metric was never committed and would be rolled back"


async def test_persists_the_turn_identifier_tokens_and_cost(
    client: AsyncClient, db: AsyncSession
) -> None:
    """
    Item 25b. The turn id is what that turn's log lines were stamped with,
    so it has to survive the round trip for a line to be joinable to the
    timings that explain it; the tokens and cost are what CLAUDE.md section
    21 asks be captured from day one.
    """

    assistant = await _make_assistant(db, "internal-turn-metrics-25b")
    turn_id = uuid.uuid4()

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={
            "call_id": str(uuid.uuid4()),
            "turn_id": str(turn_id),
            "prompt_tokens": 412,
            "completion_tokens": 37,
            "cost_micro_usd": 90,
        },
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200

    row = await db.scalar(
        select(TurnMetric).where(TurnMetric.id == uuid.UUID(response.json()["id"]))
    )

    assert row is not None
    assert row.turn_id == turn_id
    assert row.prompt_tokens == 412
    assert row.completion_tokens == 37
    assert row.cost_micro_usd == 90


async def test_accepts_a_post_from_a_voice_worker_that_sends_none_of_them(
    client: AsyncClient, db: AsyncSession
) -> None:
    """
    The two planes deploy separately and briefly run different code against
    the same schema (CLAUDE.md section 6.2). A voice worker older than these
    columns must still record the latency row it always did, with the new
    fields null - null meaning unknown, which is what they genuinely are.
    """

    assistant = await _make_assistant(db, "internal-turn-metrics-older-voice")

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={"call_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200

    row = await db.scalar(
        select(TurnMetric).where(TurnMetric.id == uuid.UUID(response.json()["id"]))
    )

    assert row is not None
    assert row.turn_id is None
    assert row.prompt_tokens is None
    assert row.cost_micro_usd is None


async def test_rejects_a_negative_token_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    """
    Not a security boundary - this channel is authenticated - but a negative
    token count is a bug upstream, and the useful moment to notice it is
    before it reaches the billing data rather than in a margin report months
    later.
    """

    assistant = await _make_assistant(db, "internal-turn-metrics-negative")

    response = await client.post(
        _TURN_METRICS_URL.format(assistant_id=assistant.id),
        json={"call_id": str(uuid.uuid4()), "completion_tokens": -1},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 422
