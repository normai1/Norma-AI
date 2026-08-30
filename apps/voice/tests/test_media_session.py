import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT
from norma_shared.speech import TranscriptEvent

import app.main as main_module
from app.main import app


async def _fake_fetch_glossary_terms(assistant_id) -> list[str]:
    return ["tinnitus", "otoscopy"]


def test_media_session_streams_partial_then_final_transcripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Sends audio into /media/session and asserts a partial transcript
    arrives before all audio has been sent, followed by a final one -
    proving real streaming (not drain-then-yield) through the actual
    Pipecat pipeline (app/media_session.py), not a hand-rolled bypass.
    """

    partial = TranscriptEvent(text="hello", is_final=False)
    final = TranscriptEvent(text="hello there", is_final=True)
    mock_stt = MockSTT(script=[partial, final], chunks_before_event=[1, 4])

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)

    assistant_id = "00000000-0000-0000-0000-000000000001"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        # Large enough to clear the output/input chunking machinery, split
        # across several sends so MockSTT's chunks_before_event has audio
        # chunks to count against.
        chunk = bytes(range(256)) * 5
        for _ in range(4):
            ws.send_bytes(chunk)

        first = json.loads(ws.receive_text())
        second = json.loads(ws.receive_text())

    assert first == {"type": "transcript", "text": "hello", "is_final": False}
    assert second == {"type": "transcript", "text": "hello there", "is_final": True}


def test_media_session_passes_glossary_terms_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # chunks_before_event=[1] (interleaved mode), not the default
    # drain-then-yield: a live connection's audio stream never ends on its
    # own, so drain-then-yield would wait forever for a StopAsyncIteration
    # that never comes while the socket stays open.
    mock_stt = MockSTT(
        script=[TranscriptEvent(text="hi", is_final=True)], chunks_before_event=[1]
    )

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)

    assistant_id = "00000000-0000-0000-0000-000000000002"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        ws.send_bytes(bytes(range(256)) * 20)
        ws.receive_text()

    assert mock_stt.received_keywords == ["tinnitus", "otoscopy"]
