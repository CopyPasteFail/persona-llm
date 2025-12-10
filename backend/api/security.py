from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Request, status

from .settings import settings

PER_MINUTE = 10
PER_DAY = 100
WINDOW_MIN = 60.0
WINDOW_DAY = 86400.0

KEY_LOGIN_MAX_PER_IP = 10
KEY_LOGIN_WINDOW_SECONDS = 600.0
KEY_LOGIN_MAX_PER_FINGERPRINT = 5
KEY_LOGIN_FINGERPRINT_WINDOW_SECONDS = 600.0

async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not x_api_key or x_api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="forbidden")

@dataclass
class Session:
    key_id: str
    label: Optional[str] = None

def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _now() -> datetime:
    return datetime.now(timezone.utc)


class SlidingWindowRateLimiter:
    """Simple in-memory limiter"""

    def __init__(self, max_hits: int, window_seconds: float):
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._hits = {}

    def hit(self, key: str, *, detail: str) -> None:
        now = time.time()
        bucket = self._hits.setdefault(key or "unknown", deque())
        cutoff = now - self.window_seconds
        # Drop expired hits
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_hits:
            raise HTTPException(status_code=429, detail=detail)
        bucket.append(now)


_chat_minute_limiter = SlidingWindowRateLimiter(PER_MINUTE, WINDOW_MIN)
_chat_day_limiter = SlidingWindowRateLimiter(PER_DAY, WINDOW_DAY)
_key_login_ip_limiter = SlidingWindowRateLimiter(KEY_LOGIN_MAX_PER_IP, KEY_LOGIN_WINDOW_SECONDS)
_key_login_fingerprint_limiter = SlidingWindowRateLimiter(
    KEY_LOGIN_MAX_PER_FINGERPRINT, KEY_LOGIN_FINGERPRINT_WINDOW_SECONDS
)


def create_session_token(key_record) -> tuple[str, datetime]:
    """Issue a short-lived session token bound to the access-key expiry."""
    if not getattr(key_record, "id", None):
        raise ValueError("key_record.id is required")
    if not getattr(key_record, "expires_at", None):
        raise ValueError("key_record.expires_at is required")

    expires_at = _ensure_aware(getattr(key_record, "expires_at"))
    session_ttl = timedelta(seconds=settings.session_ttl_seconds)
    session_exp = min(expires_at, _now() + session_ttl)

    payload = {
        "sub": str(getattr(key_record, "id")),
        "type": "session",
        "exp": int(session_exp.timestamp()),
    }
    label = getattr(key_record, "label", None)
    if label:
        payload["label"] = label

    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, session_exp

async def get_current_session(request: Request) -> Session:
    """FastAPI dependency to validate bearer token from header or optional cookie."""
    auth_header = request.headers.get("authorization")
    token: Optional[str] = None

    if auth_header:
        try:
            scheme, token_value = auth_header.split(" ", 1)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
        if scheme.lower() != "bearer" or not token_value:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
        token = token_value

    if not token and settings.session_cookie_enabled:
        token = request.cookies.get(settings.session_cookie_name)

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_token")

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")

    if payload.get("type") != "session" or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")

    return Session(key_id=str(payload["sub"]), label=payload.get("label"))

async def check_rate_limit_dependency(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _chat_minute_limiter.hit(ip, detail="rate limit exceeded")
    _chat_day_limiter.hit(ip, detail="rate limit exceeded")


def enforce_key_login_rate_limits(request: Request, *, key_fingerprint: Optional[str] = None) -> None:
    """Limit key-login attempts per IP and per fingerprint before bcrypt verification."""
    ip = request.client.host if request.client else "unknown"
    _key_login_ip_limiter.hit(ip, detail="rate_limited")
    if key_fingerprint:
        _key_login_fingerprint_limiter.hit(key_fingerprint, detail="rate_limited")
