import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.tokens import decode_access_token
from app.models.user import User
from app.providers.factory import (
    get_storage_provider_dependency,
    get_tts_provider_dependency,
)
from app.providers.httpx_web_crawler import get_page_fetcher_dependency
from app.providers.speech import TextToSpeechProvider
from app.providers.storage import StorageProvider
from app.providers.web_crawler import PageFetcher
from app.services import auth as auth_service

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]

RedisClient = Annotated[Redis, Depends(get_redis)]

TtsProvider = Annotated[TextToSpeechProvider, Depends(get_tts_provider_dependency)]

StorageProviderDep = Annotated[
    StorageProvider,
    Depends(get_storage_provider_dependency),
]

PageFetcherDep = Annotated[
    PageFetcher,
    Depends(get_page_fetcher_dependency),
]

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User:
    """
    Resolve the signed-in user from a bearer access token.
    """

    if credentials is None:
        raise _CREDENTIALS_ERROR

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _CREDENTIALS_ERROR from exc

    user = await auth_service.get_active_user(db, user_id)

    if user is None:
        raise _CREDENTIALS_ERROR

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
