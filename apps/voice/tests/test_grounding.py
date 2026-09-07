"""
Item 24b end to end: what the caller actually hears when the model asserts
something it was never given.

Asserts on the reply that reaches the wire, not on internal state - a
validator that flags correctly but never changes what is spoken would pass a
unit test and fail the caller.
"""

import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.guardrails import SAFE_FALLBACK
from app.main import app
from app.mock_llm import MockLLM
from tests.conftest import (
    _fake_fetch_glossary_terms,
    _fake_fetch_turn_sensitivity,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _ScriptedVADAnalyzer,
)

_KNOWLEDGE = "A standard session costs $50. We open at 9am."


def _spoken_reply(monkeypatch, *, reply: str, knowledge: str) -> str:
    """The assistant's completed reply text for one turn."""

    mock_stt = MockSTT(
        script=[TranscriptEvent(text="How much is a session?", is_final=True)],
        chunks_before_event=[1],
    )
    mock_llm = MockLLM(response=reply, chunk_words=3)

    async def _retrieved(assistant_id, query) -> str:
        return knowledge

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    monkeypatch.setattr(main_module, "get_tts_provider", lambda: MockTTS())
    monkeypatch.setattr(media_session_module, "fetch_retrieved_context", _retrieved)
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url("00000000-0000-0000-0000-0000000000b1")) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            message = json.loads(ws.receive_text())

            if message["type"] == "llm_complete":
                return message["text"]

            if message["type"] in {"llm_error", "session_failover"}:
                pytest.fail(f"turn failed before completing: {message['type']}")


def test_a_price_the_knowledge_does_not_contain_is_not_spoken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The headline case: the caller is told the assistant doesn't have the
    detail, rather than being quoted a figure nobody set.
    """

    spoken = _spoken_reply(
        monkeypatch,
        reply="A session costs $250. Shall I book you in?",
        knowledge=_KNOWLEDGE,
    )

    assert SAFE_FALLBACK in spoken
    assert "$250" not in spoken


def test_the_rest_of_a_reply_is_abandoned_after_a_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A reply that has already invented one fact is not trusted to finish - and
    stitching the fallback into the middle of an answer reads as incoherent.
    """

    spoken = _spoken_reply(
        monkeypatch,
        reply="A session costs $250. We also offer evening slots. Call back anytime.",
        knowledge=_KNOWLEDGE,
    )

    assert "evening slots" not in spoken
    assert "Call back anytime" not in spoken


def test_a_price_the_knowledge_does_contain_is_spoken_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The half that matters most - a correct answer must survive untouched, or
    the guardrail has made the assistant useless.
    """

    spoken = _spoken_reply(
        monkeypatch,
        reply="A standard session costs $50.",
        knowledge=_KNOWLEDGE,
    )

    assert "$50" in spoken
    assert SAFE_FALLBACK not in spoken


def test_claiming_to_have_booked_is_not_spoken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spoken = _spoken_reply(
        monkeypatch,
        reply="I've booked you in for Tuesday at 9am.",
        knowledge=_KNOWLEDGE,
    )

    assert SAFE_FALLBACK in spoken
    assert "booked you in" not in spoken


def test_an_ordinary_answer_is_spoken_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    No numbers, no claims - the common case, and it must pass straight
    through including across sentence boundaries.
    """

    spoken = _spoken_reply(
        monkeypatch,
        reply="We're open Monday to Friday. I can take a message if you'd like.",
        knowledge=_KNOWLEDGE,
    )

    assert "Monday to Friday" in spoken
    assert "take a message" in spoken
    assert SAFE_FALLBACK not in spoken
