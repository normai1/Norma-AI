from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.client_info import client_ip, client_user_agent
from app.api.deps import CurrentUser, DbSession, RedisClient
from app.core.exceptions import (
    EmailAlreadyRegistered,
    InactiveAccount,
    InvalidCredentials,
    InvalidRefreshToken,
)
from app.core.rate_limit import (
    LOGIN_RATE_LIMIT,
    REGISTER_RATE_LIMIT,
    RateLimitExceeded,
    RateLimitRule,
    enforce,
)
from app.core.security import normalize_email
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    UserResponse,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
)

_INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired refresh token",
)


async def _rate_limit(
    redis: RedisClient,
    key: str,
    rule: RateLimitRule,
) -> None:
    """
    Apply a rate-limit rule, translating exhaustion into a 429.
    """

    try:
        await enforce(redis, key, rule)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: DbSession,
    redis: RedisClient,
) -> AuthResponse:
    """
    Create an account and sign the new user in.
    """

    await _rate_limit(
        redis,
        f"register:{client_ip(request)}",
        REGISTER_RATE_LIMIT,
    )

    try:
        user = await auth_service.register(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from exc

    tokens = await auth_service.issue_tokens(
        db,
        user=user,
        user_agent=client_user_agent(request),
        ip_address=client_ip(request),
    )

    await db.commit()

    return AuthResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DbSession,
    redis: RedisClient,
) -> AuthResponse:
    """
    Exchange an email and password for a token pair.
    """

    await _rate_limit(
        redis,
        f"login:{client_ip(request)}:{normalize_email(payload.email)}",
        LOGIN_RATE_LIMIT,
    )

    try:
        user = await auth_service.authenticate(
            db,
            email=payload.email,
            password=payload.password,
        )
    except InvalidCredentials as exc:
        raise _INVALID_CREDENTIALS from exc
    except InactiveAccount as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        ) from exc

    tokens = await auth_service.issue_tokens(
        db,
        user=user,
        user_agent=client_user_agent(request),
        ip_address=client_ip(request),
    )

    await db.commit()

    return AuthResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: DbSession,
) -> AuthResponse:
    """
    Rotate a refresh token into a fresh token pair.
    """

    try:
        user, tokens = await auth_service.refresh(
            db,
            refresh_token=payload.refresh_token,
            user_agent=client_user_agent(request),
            ip_address=client_ip(request),
        )
    except InvalidRefreshToken as exc:
        await db.commit()

        raise _INVALID_REFRESH from exc

    await db.commit()

    return AuthResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserResponse.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    db: DbSession,
) -> Response:
    """
    Revoke the session behind a refresh token.
    """

    await auth_service.logout(db, refresh_token=payload.refresh_token)
    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: CurrentUser,
) -> UserResponse:
    """
    Return the signed-in user's profile.
    """

    return UserResponse.model_validate(current_user)
