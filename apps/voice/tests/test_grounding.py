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


def _spoken_reply(
    monkeypatch, *, reply: str, knowledge: str, caller_says: str = "How much is a session?"
) -> str:
    """The assistant's completed reply text for one turn."""

    mock_stt = MockSTT(
        script=[TranscriptEvent(text=caller_says, is_final=True)],
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


def test_the_assistant_may_read_back_what_the_caller_just_told_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The regression for a real call, and for the one skill that has nothing to
    do with the knowledge base.

    The caller gave their phone number and their email address; the assistant
    read them back to confirm, which is the whole of taking a message; and
    both replies were blocked as "unsupported number", because digits the
    caller had just spoken were not in any crawled page. What they heard, in
    answer to their own email address, was "I don't have that detail in front
    of me right now."

    Repeating what the caller said is not a claim about the business and
    cannot be an invention - they are the source.
    """

    spoken = _spoken_reply(
        monkeypatch,
        caller_says="My phone number is 7400294369.",
        reply="Thanks, I have your number as 7400294369.",
        knowledge=_KNOWLEDGE,
    )

    assert "7400294369" in spoken
    assert SAFE_FALLBACK not in spoken


def test_the_knowledge_bar_still_applies_to_claims_about_the_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The other half: letting the caller's words ground a reply must not let
    the assistant invent a price merely because a number was mentioned on
    the call. A figure the caller never said and the knowledge never
    contained is still refused.
    """

    spoken = _spoken_reply(
        monkeypatch,
        caller_says="My phone number is 7400294369.",
        reply="Certainly. A session costs $250.",
        knowledge=_KNOWLEDGE,
    )

    assert SAFE_FALLBACK in spoken
    assert "$250" not in spoken
