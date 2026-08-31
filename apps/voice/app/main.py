import uuid

from fastapi import FastAPI, WebSocket
from pipecat.frames.frames import EndFrame
from pipecat.workers.runner import WorkerRunner

from app.glossary_client import fetch_glossary_terms
from app.llm_config_client import fetch_llm_config
from app.llm_provider_factory import get_llm_provider
from app.media_session import build_voice_session_pipeline_worker
from app.provider_factory import get_stt_provider, get_tts_provider
from app.tts_config_client import fetch_tts_config
from app.turn_detection_client import fetch_turn_sensitivity

app = FastAPI(title="Norma AI Voice")

# Placeholder until item 20 (real-time voice session engine) gives this a real
# session registry. The shape is the contract the deployment platform's
# scaling and drain decisions read from (CLAUDE.md section 18).
_PLACEHOLDER_CAPACITY = 10


@app.get("/health")
async def health() -> dict[str, object]:
    """
    Report liveness and session capacity for scaling/drain decisions.
    """

    return {
        "status": "ok",
        "active_sessions": 0,
        "capacity": _PLACEHOLDER_CAPACITY,
    }


@app.websocket("/media/session")
async def media_session(
    websocket: WebSocket,
    assistant_id: uuid.UUID,
    language: str = "en",
) -> None:
    """
    Items 20b-20e's full turn-loop proof: accepts a WebSocket connection,
    fetches the assistant's glossary terms, turn sensitivity, LLM config,
    and TTS config, wires the connection into a Pipecat pipeline that
    transcribes incoming audio, detects when a turn has ended, streams an
    LLM reply, and speaks it - with immediate cancellation on barge-in (see
    app/media_session.py) - and runs it until the caller disconnects. Not a
    real call - 20f-20g add latency instrumentation and resilience; this
    route exists to prove the turn loop itself works end to end.
    """

    await websocket.accept()

    # Session-scoped placeholder call identity for item 20f's TurnMetric
    # rows - Call (build-plan item 26) doesn't exist yet.
    call_id = uuid.uuid4()

    keywords = await fetch_glossary_terms(assistant_id)
    sensitivity = await fetch_turn_sensitivity(assistant_id)
    llm_config = await fetch_llm_config(assistant_id)
    tts_config = await fetch_tts_config(assistant_id)
    provider = get_stt_provider()
    llm_provider = get_llm_provider()
    tts_provider = get_tts_provider()
    worker = build_voice_session_pipeline_worker(
        websocket,
        provider,
        llm_provider,
        tts_provider,
        assistant_id=assistant_id,
        call_id=call_id,
        language=language,
        keywords=keywords,
        sensitivity=sensitivity,
        system_prompt=llm_config.system_prompt,
        creativity=llm_config.creativity,
        voice_id=tts_config.voice_id,
        speech_rate=tts_config.speech_rate,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    @worker.event_handler("on_pipeline_finished")
    async def _on_pipeline_finished(_worker: object, frame: object) -> None:
        # Item 20g: an EndFrame pushed from *inside* the pipeline (a
        # session-failover apology finishing) reaches the sink and ends the
        # pipeline's own internal processing, but the WorkerRunner itself
        # only stops on an *external* signal - runner.run() below would
        # otherwise never return, and the WebSocket would never close.
        # Verified empirically: without this handler, the connection hangs
        # indefinitely once EndFrame reaches the end of the pipeline,
        # confirmed via a direct receive-loop script against a real
        # session. runner.end() (graceful), not runner.cancel() (abrupt) -
        # cancel() was tried first and, while it does close the connection,
        # it does so by cancelling the runner's own task, which surfaced as
        # a raw CancelledError out of TestClient's __exit__ in the test
        # suite; end() is the semantically-correct call for a pipeline that
        # is ending on its own terms, and does not have that problem.
        # StopFrame/CancelFrame terminal states already have their own
        # external trigger (a caller disconnecting), so only EndFrame needs
        # this.
        if isinstance(frame, EndFrame):
            await runner.end(reason="session ended")

    await runner.run()
