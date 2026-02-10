from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .keys import compute_key_fingerprint, verify_plain_key_and_get_record
from .security import create_session_token, enforce_key_login_rate_limits
from .settings import settings

router = APIRouter()


class KeyLoginRequest(BaseModel):
    key: str = Field(..., min_length=1)


class KeyLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


@router.post("/auth/key-login", response_model=KeyLoginResponse)
async def key_login(payload: KeyLoginRequest, request: Request) -> JSONResponse:
    fingerprint = compute_key_fingerprint(payload.key)
    enforce_key_login_rate_limits(request=request, key_fingerprint=fingerprint)
    key_record = verify_plain_key_and_get_record(payload.key)
    token, expires_at = create_session_token(key_record)
    body = KeyLoginResponse(access_token=token, token_type="bearer", expires_at=expires_at)

    response = JSONResponse(content=jsonable_encoder(body))
    if settings.session_cookie_enabled:
        max_age = max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
        response.set_cookie(
            key=settings.session_cookie_name,
            value=token,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
            path=settings.session_cookie_path,
            max_age=max_age,
            expires=expires_at,
        )

    return response


@router.post("/auth/logout", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    if settings.session_cookie_enabled:
        response.delete_cookie(
            key=settings.session_cookie_name,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
            path=settings.session_cookie_path,
        )
    return response
