import uuid

from fastapi import FastAPI, WebSocket
from pipecat.workers.runner import WorkerRunner

from app.glossary_client import fetch_glossary_terms
from app.media_session import build_speech_to_text_pipeline_worker
from app.provider_factory import get_stt_provider

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
    Item 20b's streaming-STT proof: accepts a WebSocket connection, fetches
    the assistant's glossary terms for keyword biasing, wires the
    connection into a Pipecat pipeline that transcribes incoming audio
    (see app/media_session.py), and runs it until the caller disconnects.
    Not a real call - 20c-20g add turn detection, the LLM loop, TTS/
    barge-in, latency instrumentation, and resilience; this route exists
    to prove real streaming transcription works.
    """

    await websocket.accept()

    keywords = await fetch_glossary_terms(assistant_id)
    provider = get_stt_provider()
    worker = build_speech_to_text_pipeline_worker(
        websocket, provider, language=language, keywords=keywords
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    await runner.run()
