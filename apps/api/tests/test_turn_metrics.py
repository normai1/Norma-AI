import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.assistant import Assistant
from app.models.organization import Organization
from app.models.turn_metric import TurnMetric
from app.models.workspace import Workspace
from app.services.turn_metrics import (
    compute_time_to_first_audio_p95,
    record_turn_metric,
)


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


async def test_record_turn_metric_persists_the_resolved_scope_and_every_leg(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "turn-metrics-full")
    call_id = uuid.uuid4()
    now = datetime.now(UTC)

    row = await record_turn_metric(
        db,
        assistant_id=assistant.id,
        call_id=call_id,
        stt_finalized_at=now,
        retrieval_done_at=now + timedelta(milliseconds=10),
        llm_first_token_at=now + timedelta(milliseconds=200),
        llm_complete_at=now + timedelta(milliseconds=600),
        tts_first_byte_at=now + timedelta(milliseconds=250),
        audio_out_at=now + timedelta(milliseconds=260),
    )

    assert row.organization_id == assistant.organization_id
    assert row.workspace_id == assistant.workspace_id
    assert row.assistant_id == assistant.id
    assert row.call_id == call_id
    assert row.stt_finalized_at == now
    assert row.audio_out_at == now + timedelta(milliseconds=260)


async def test_record_turn_metric_allows_a_partial_row(db: AsyncSession) -> None:
    """
    An interrupted or failed turn legitimately never reaches every leg -
    "every turn writes a row" does not mean every row is complete.
    """

    assistant = await _make_assistant(db, "turn-metrics-partial")
    now = datetime.now(UTC)

    row = await record_turn_metric(
        db,
        assistant_id=assistant.id,
        call_id=uuid.uuid4(),
        stt_finalized_at=now,
    )

    assert row.stt_finalized_at == now
    assert row.retrieval_done_at is None
    assert row.llm_first_token_at is None
    assert row.audio_out_at is None


async def test_record_turn_metric_raises_for_an_unknown_assistant(
    db: AsyncSession,
) -> None:
    with pytest.raises(AssistantNotFound):
        await record_turn_metric(db, assistant_id=uuid.uuid4(), call_id=uuid.uuid4())


def _row(*, stt_finalized_at=None, audio_out_at=None) -> TurnMetric:
    row = TurnMetric(
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        assistant_id=uuid.uuid4(),
        call_id=uuid.uuid4(),
        stt_finalized_at=stt_finalized_at,
        audio_out_at=audio_out_at,
    )

    return row


def test_compute_time_to_first_audio_p95_uses_only_complete_rows() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        _row(stt_finalized_at=base, audio_out_at=base + timedelta(milliseconds=500)),
        _row(stt_finalized_at=base, audio_out_at=base + timedelta(milliseconds=600)),
        _row(stt_finalized_at=base, audio_out_at=None),  # never reached audio_out
        _row(stt_finalized_at=None, audio_out_at=base + timedelta(milliseconds=999)),
    ]

    p95 = compute_time_to_first_audio_p95(rows)

    assert p95 == 600.0


def test_compute_time_to_first_audio_p95_is_none_when_no_row_is_complete() -> None:
    rows = [_row(stt_finalized_at=datetime.now(UTC), audio_out_at=None)]

    assert compute_time_to_first_audio_p95(rows) is None


def test_compute_time_to_first_audio_p95_is_none_for_no_rows() -> None:
    assert compute_time_to_first_audio_p95([]) is None
