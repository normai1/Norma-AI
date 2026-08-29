from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.assistant_versions import router as assistant_versions_router
from app.api.v1.assistants import router as assistants_router
from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.invitations import router as invitations_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.voices import router as voices_router
from app.api.v1.workspaces import router as workspaces_router
from app.core.config import settings
from app.core.redis import redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup/shutdown lifecycle.
    """

    # Startup
    await redis.ping()

    yield

    # Shutdown
    await redis.aclose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(
    health_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    auth_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    organizations_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    invitations_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    workspaces_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    voices_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    assistants_router,
    prefix=settings.api_v1_prefix,
)

app.include_router(
    assistant_versions_router,
    prefix=settings.api_v1_prefix,
)


@app.get("/")
async def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }
