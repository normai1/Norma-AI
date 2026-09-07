"""
Item 24c end to end: a blocked subject is refused without the model ever
being asked.

The assertion that matters is `MockLLM.call_count == 0` - it is what makes
this enforcement rather than instruction. A prompt asking the model to refuse
would still call it, and could still be argued out of refusing.
"""

import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.guardrails import BLOCKED_TOPIC_REPLY
from app.main import app
from app.mock_llm import MockLLM
from tests.conftest import (
    _fake_fetch_glossary_terms,
    _fake_fetch_retrieved_context,
    _fake_fetch_turn_sensitivity,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _ScriptedVADAnalyzer,
)


def _turn(monkeypatch, *, caller_says: str, blocked: list[str]) -> tuple[str, MockLLM]:
    """One turn with the given blocked topics; returns the reply and the LLM."""

    mock_stt = MockSTT(
        script=[TranscriptEvent(text=caller_says, is_final=True)],
        chunks_before_event=[1],
    )
    mock_llm = MockLLM(response="Here is a detailed answer.")

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch, blocked_topics=blocked)
    monkeypatch.setattr(main_module, "get_tts_provider", lambda: MockTTS())
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url("00000000-0000-0000-0000-0000000000c1")) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            message = json.loads(ws.receive_text())

            if message["type"] == "llm_complete":
                return message["text"], mock_llm


def test_a_blocked_subject_is_refused_without_calling_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply, llm = _turn(
        monkeypatch,
        caller_says="Can you give me legal advice about my contract?",
        blocked=["legal advice"],
    )

    assert reply == BLOCKED_TOPIC_REPLY
    # The point of enforcing on input: the model never saw the request, so no
    # prompt wording and no caller persistence can talk it into answering.
    assert llm.call_count == 0


def test_an_unrelated_question_is_answered_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply, llm = _turn(
        monkeypatch,
        caller_says="What are your opening hours?",
        blocked=["legal advice"],
    )

    assert reply == "Here is a detailed answer."
    assert llm.call_count == 1


def test_an_assistant_with_no_blocked_topics_is_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Blocking is opt-in: the default configuration must behave exactly as it
    did before this feature existed.
    """

    reply, llm = _turn(
        monkeypatch,
        caller_says="Can you give me legal advice about my contract?",
        blocked=[],
    )

    assert reply == "Here is a detailed answer."
    assert llm.call_count == 1
