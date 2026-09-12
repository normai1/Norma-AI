"""
Item 25b, driven through the real pipeline rather than its parts.

The unit tests around this prove that a recorder mints turn ids, that a
provider reports usage, and that a price turns usage into money. None of
that proves the three are wired to each other, which is the failure this
file exists to catch: a turn can complete, be posted, and carry no
identifier and no cost at all while every unit test still passes.

Uses the same session harness as test_media_session.py - scripted VAD, mock
STT/LLM/TTS, no network - so what runs here is the production pipeline with
its providers swapped, not a re-implementation of it.
"""

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT
from norma_shared.speech import TranscriptEvent
from norma_shared.token_cost import TokenUsage
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app import config
from app.main import app
from app.mock_llm import MockLLM
from tests.conftest import (
    _capturing_record_turn_metric,
    _fake_fetch_glossary_terms,
    _fake_fetch_retrieved_context,
    _fake_fetch_turn_sensitivity,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _receive_one,
    _ScriptedVADAnalyzer,
    _silent_tts,
)

_ASSISTANT_ID = "00000000-0000-0000-0000-0000000025b0"
_MODEL = "a-priced-test-model"


def _run_one_turn(monkeypatch: pytest.MonkeyPatch, mock_llm: MockLLM):
    """
    Drive a single complete turn and return whatever was posted for it.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)

    monkeypatch.setattr(
        main_module,
        "get_stt_provider",
        lambda: MockSTT(script=[final], chunks_before_event=[1]),
    )
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(
        main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity
    )
    _patch_session_setup(monkeypatch)
    monkeypatch.setattr(main_module, "get_tts_provider", _silent_tts)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )
    calls = _capturing_record_turn_metric(monkeypatch)

    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(_ASSISTANT_ID)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            entry = _receive_one(ws)

            if entry[0] == "text" and entry[1]["type"] == "reply_finished":
                break

    assert len(calls) == 1

    return calls[0][1]


def test_a_real_turn_is_posted_with_its_own_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The identifier stamped on that turn's log lines has to reach the row, or
    a line and the timings that explain it cannot be joined.
    """

    record = _run_one_turn(monkeypatch, MockLLM(response="We open at nine."))

    assert record.turn_id is not None
    assert record.call_id != record.turn_id


def test_a_real_turn_carries_the_tokens_and_cost_its_provider_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    400 prompt tokens at $0.15/M plus 40 completion tokens at $0.75/M is
    $0.00009, which is 90 micro-dollars.
    """

    monkeypatch.setattr(config, "LLM_REALTIME_MODEL", _MODEL)
    monkeypatch.setattr(config, "LLM_REALTIME_INPUT_USD_PER_MTOK", "0.15")
    monkeypatch.setattr(config, "LLM_REALTIME_OUTPUT_USD_PER_MTOK", "0.75")

    record = _run_one_turn(
        monkeypatch,
        MockLLM(
            response="We open at nine.",
            usage=TokenUsage(prompt_tokens=400, completion_tokens=40),
        ),
    )

    assert record.prompt_tokens == 400
    assert record.completion_tokens == 40
    assert record.cost_micro_usd == 90


def test_a_turn_whose_provider_reports_nothing_still_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The default MockLLM reports no usage, which is also what a provider
    that simply does not send it does. The caller still gets their answer,
    the row is still written, and the cost is null rather than zero.
    """

    record = _run_one_turn(monkeypatch, MockLLM(response="We open at nine."))

    assert record.audio_out_at is not None or record.llm_complete_at is not None
    assert record.prompt_tokens is None
    assert record.cost_micro_usd is None
