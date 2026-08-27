from fastapi import FastAPI

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
