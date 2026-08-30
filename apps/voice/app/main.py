import uuid

from fastapi import FastAPI, WebSocket
from pipecat.workers.runner import WorkerRunner

from app.glossary_client import fetch_glossary_terms
from app.llm_config_client import fetch_llm_config
from app.llm_provider_factory import get_llm_provider
from app.media_session import build_voice_session_pipeline_worker
from app.provider_factory import get_stt_provider
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
    Items 20b-20d's streaming-STT-plus-turn-detection-plus-LLM-reply proof:
    accepts a WebSocket connection, fetches the assistant's glossary terms,
    turn sensitivity, and LLM config, wires the connection into a Pipecat
    pipeline that transcribes incoming audio, detects when a turn has
    ended, and streams an LLM reply (see app/media_session.py), and runs it
    until the caller disconnects. Not a real call - 20e-20g add TTS/
    barge-in, latency instrumentation, and resilience; this route exists to
    prove the turn loop itself works.
    """

    await websocket.accept()

    keywords = await fetch_glossary_terms(assistant_id)
    sensitivity = await fetch_turn_sensitivity(assistant_id)
    llm_config = await fetch_llm_config(assistant_id)
    provider = get_stt_provider()
    llm_provider = get_llm_provider()
    worker = build_voice_session_pipeline_worker(
        websocket,
        provider,
        llm_provider,
        assistant_id=assistant_id,
        language=language,
        keywords=keywords,
        sensitivity=sensitivity,
        system_prompt=llm_config.system_prompt,
        creativity=llm_config.creativity,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    await runner.run()
