from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, cast

from fastapi import APIRouter, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .keys import compute_key_fingerprint, verify_plain_key_and_get_record
from .security import create_session_token, enforce_key_login_rate_limits
from .settings import settings

MIN_COOKIE_MAX_AGE_SECONDS = 1
TOKEN_TYPE_BEARER = "bearer"

router = APIRouter()

CookieSameSite = Literal["lax", "strict", "none"]


def _session_cookie_samesite() -> CookieSameSite:
    """Return the validated SameSite value for session cookies.

    Inputs:
        None.
    Outputs:
        A Literal value accepted by FastAPI's cookie helpers.
    Edge cases:
        Relies on settings normalization to ensure a safe default.
    Atomicity/concurrency:
        Pure read; no shared state updates.
    """
    return cast(CookieSameSite, settings.session_cookie_samesite)


class KeyLoginRequest(BaseModel):
    key: str = Field(..., min_length=1)


class KeyLoginResponse(BaseModel):
    access_token: str
    token_type: str = TOKEN_TYPE_BEARER
    expires_at: datetime


@router.post("/auth/key-login", response_model=KeyLoginResponse)
async def key_login(payload: KeyLoginRequest, request: Request) -> JSONResponse:
    """Create a session for a valid API key and return a bearer token response.

    Inputs:
        payload: Request body containing the plaintext API key.
        request: Incoming HTTP request used for rate limiting.
    Outputs:
        JSONResponse with a bearer token payload and optional session cookie.
    Edge cases:
        Raises when the API key is invalid or when rate limits are exceeded.
        If the cookie max-age would be non-positive, it is clamped to a minimum.
    Atomicity/concurrency:
        Token issuance is handled by the session token generator; this handler
        performs no multi-step writes.
    """
    fingerprint = compute_key_fingerprint(payload.key)
    enforce_key_login_rate_limits(request=request, key_fingerprint=fingerprint)
    key_record = verify_plain_key_and_get_record(payload.key)
    token, expires_at = create_session_token(key_record)
    body = KeyLoginResponse(
        access_token=token,
        token_type=TOKEN_TYPE_BEARER,
        expires_at=expires_at,
    )

    response = JSONResponse(content=jsonable_encoder(body))
    if settings.session_cookie_enabled:
        max_age = max(
            MIN_COOKIE_MAX_AGE_SECONDS,
            int((expires_at - datetime.now(timezone.utc)).total_seconds()),
        )
        response.set_cookie(
            key=settings.session_cookie_name,
            value=token,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite=_session_cookie_samesite(),
            path=settings.session_cookie_path,
            max_age=max_age,
            expires=expires_at,
        )

    return response


@router.post("/auth/logout", status_code=204)
async def logout() -> Response:
    """Clear the session cookie for the current client.

    Inputs:
        None.
    Outputs:
        Empty 204 Response, with cookie deletion when enabled.
    Edge cases:
        If cookies are disabled in settings, this becomes a no-op.
    Atomicity/concurrency:
        Single response mutation; no shared state updates.
    """
    response = Response(status_code=204)
    if settings.session_cookie_enabled:
        response.delete_cookie(
            key=settings.session_cookie_name,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite=_session_cookie_samesite(),
            path=settings.session_cookie_path,
        )
    return response
