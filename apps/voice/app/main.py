from fastapi import FastAPI, WebSocket
from pipecat.workers.runner import WorkerRunner

from app.media_session import build_echo_pipeline_worker

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


@app.websocket("/media/echo")
async def media_echo(websocket: WebSocket) -> None:
    """
    Item 20a's bidirectional-streaming-audio proof: accepts a WebSocket
    connection, wires it into a minimal Pipecat echo pipeline (see
    app/media_session.py), and runs it until the caller disconnects. Not a
    real call - 20b-20g replace the echo stage with STT/turn-detection/LLM/
    TTS and add resilience; this route exists to prove the plumbing works.
    """

    await websocket.accept()

    worker = build_echo_pipeline_worker(websocket)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    await runner.run()
