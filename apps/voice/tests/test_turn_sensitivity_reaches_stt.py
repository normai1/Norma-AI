"""
The operator's turn-sensitivity setting has to govern the speech provider's
own end-of-turn decision, not just Norma's.

A turn ends when both detectors agree the caller has stopped, so the slower
one decides - and the provider's own default was always the slower one. At
the default sensitivity of 0.5 Norma's VAD calls the turn over after 0.9s
of silence while ElevenLabs waited a fixed 1.5s, so the setting made no
difference to anything a caller could perceive. Measured against the live
API at true realtime audio pacing, end of speech to committed transcript:

    1.5s (the API's default): 1.83s
    0.8s:                     1.06s
    0.5s (the API's floor):   0.73s
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import CancelFrame, InputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

import app.main as main_module
from app.main import app
from app.media_session import SpeechToTextProcessor
from app.turn_detection import sensitivity_to_stop_secs
from tests.conftest import (
    _fake_fetch_glossary_terms,
    _fake_fetch_turn_sensitivity,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _ScriptedVADAnalyzer,
)


async def _noop_warm(assistant_id) -> None:
    """Session start fires this at the API; tests have no API to call."""


async def _swallow(frame, direction=None) -> None:
    """Nothing downstream in these tests; only the provider call matters."""


@pytest.mark.parametrize(
    ("sensitivity", "expected_stop_secs"),
    [
        (0.0, 1.5),
        (0.5, 0.9),
        (1.0, 0.3),
    ],
)
def test_sensitivity_maps_to_one_number_used_by_both_detectors(
    sensitivity: float, expected_stop_secs: float
) -> None:
    """
    One mapping, one number. Two detectors deriving their patience
    separately is what let them disagree in the first place.
    """

    assert sensitivity_to_stop_secs(sensitivity) == pytest.approx(expected_stop_secs)


async def _run_one_stream(processor: SpeechToTextProcessor) -> None:
    """
    Start the processor, feed it a frame, and let it tear down - enough for
    its stream task to reach the provider once.
    """

    task_manager = TaskManager()
    await processor.setup(
        FrameProcessorSetup(
            clock=SystemClock(), task_manager=task_manager, pipeline_worker=None
        )
    )

    await processor.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(
        InputAudioRawFrame(audio=b"\0" * 320, sample_rate=16000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    await processor.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.05)
    await processor.cleanup()


async def test_the_operator_s_sensitivity_reaches_the_speech_provider() -> None:
    """
    The end-to-end point of the change: what the operator chose in the UI is
    what the provider is told, rather than the provider's own default
    silently deciding when every turn ends.
    """

    provider = MockSTT()
    processor = SpeechToTextProcessor(
        provider,
        language="en",
        silence_threshold_secs=sensitivity_to_stop_secs(0.5),
    )
    processor.push_frame = _swallow

    await _run_one_stream(processor)

    assert provider.received_silence_threshold_secs == pytest.approx(0.9)


async def test_a_processor_given_no_threshold_leaves_the_provider_alone() -> None:
    """
    None must keep the provider's own default, so nothing that constructs
    this processor without an opinion silently changes turn timing.
    """

    provider = MockSTT()
    processor = SpeechToTextProcessor(provider, language="en")
    processor.push_frame = _swallow

    await _run_one_stream(processor)

    assert provider.received_silence_threshold_secs is None


def test_a_real_session_tells_the_provider_the_operator_s_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Through the actual pipeline, not the processor in isolation: the
    session's configured sensitivity has to survive
    build_voice_session_pipeline_worker and land on the provider call.
    Without this, the derivation could be dropped there and every unit
    test above would still pass.

    The conftest fake reports sensitivity 0.5, which maps to 0.9s.
    """

    mock_stt = MockSTT(
        script=[TranscriptEvent(text="hi", is_final=True)], chunks_before_event=[1]
    )

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(
        main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity
    )
    monkeypatch.setattr(main_module, "warm_retrieval_cache", _noop_warm)
    _patch_session_setup(monkeypatch)
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))

    assistant_id = "00000000-0000-0000-0000-000000000001"

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        ws.send_bytes(bytes(range(256)) * 5)
        json.loads(ws.receive_text())

    assert mock_stt.received_silence_threshold_secs == pytest.approx(
        sensitivity_to_stop_secs(0.5)
    )
