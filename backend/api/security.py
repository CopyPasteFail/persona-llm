from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Optional, Protocol, cast

import jwt as _jwt
from fastapi import Header, HTTPException, Request, status

from .settings import settings

AUTHORIZATION_HEADER_NAME = "authorization"
API_KEY_HEADER_NAME = "x-api-key"
BEARER_AUTH_SCHEME = "bearer"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_FIELD = "exp"
JWT_LABEL_FIELD = "label"
JWT_SUBJECT_FIELD = "sub"
JWT_TYPE_FIELD = "type"
JWT_TYPE_SESSION = "session"
UNKNOWN_CLIENT_IP = "unknown"

ERROR_FORBIDDEN = "forbidden"
ERROR_INVALID_TOKEN = "invalid_token"
ERROR_MISSING_TOKEN = "missing_token"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_RATE_LIMIT_EXCEEDED = "rate limit exceeded"
ERROR_TOKEN_EXPIRED = "token_expired"

CHAT_RATE_LIMIT_MAX_PER_MINUTE = 10
CHAT_RATE_LIMIT_MAX_PER_DAY = 100
CHAT_RATE_LIMIT_WINDOW_SECONDS_PER_MINUTE = 60.0
CHAT_RATE_LIMIT_WINDOW_SECONDS_PER_DAY = 86_400.0

KEY_LOGIN_MAX_PER_IP = 10
KEY_LOGIN_WINDOW_SECONDS = 600.0
KEY_LOGIN_MAX_PER_FINGERPRINT = 5
KEY_LOGIN_FINGERPRINT_WINDOW_SECONDS = 600.0


class _JwtModule(Protocol):
    ExpiredSignatureError: type[Exception]
    InvalidTokenError: type[Exception]

    def encode(
        self,
        payload: Mapping[str, Any],
        key: str | bytes,
        *,
        algorithm: str | None = None,
        **kwargs: Any,
    ) -> str:
        ...

    def decode(
        self,
        encoded_token: str | bytes,
        key: str | bytes = "",
        *,
        algorithms: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ...


jwt = cast(_JwtModule, _jwt)


async def verify_api_key(
    api_key_header_value: Optional[str] = Header(None, alias=API_KEY_HEADER_NAME),
) -> None:
    """Validate the API key header value against configured credentials.

    Inputs:
        api_key_header_value: The `x-api-key` header value, if present.

    Outputs:
        None. Raises an HTTPException when validation fails.

    Edge cases:
        Missing or empty header values are treated as invalid.
    """
    if not api_key_header_value or api_key_header_value != settings.API_KEY:
        raise HTTPException(status_code=403, detail=ERROR_FORBIDDEN)


@dataclass
class Session:
    """Represent an authenticated session derived from a session token."""

    key_id: str
    label: Optional[str] = None


class _KeyRecord(Protocol):
    id: str
    expires_at: datetime
    label: Optional[str]


def _ensure_utc_aware(timestamp: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Inputs:
        timestamp: The datetime to normalize.

    Outputs:
        A UTC-aware datetime.

    Edge cases:
        Naive datetimes are assumed to be UTC.
    """
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class SlidingWindowRateLimiter:
    """Enforce a sliding-window rate limit using in-memory hit buckets."""

    def __init__(self, max_hits: int, window_seconds: float):
        """Initialize the limiter with a max hits and window size.

        Inputs:
            max_hits: Maximum number of hits allowed in the window.
            window_seconds: Window duration in seconds.

        Outputs:
            None.
        """
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def hit(self, rate_limit_key: str, *, error_detail: str) -> None:
        """Record a hit and raise an HTTPException if the limit is exceeded.

        Inputs:
            rate_limit_key: Identifier for the entity being rate limited.
            error_detail: Error detail to include in the HTTPException.

        Outputs:
            None. Raises an HTTPException when the limit is exceeded.

        Edge cases:
            Empty keys are normalized to a placeholder.
        """
        current_timestamp_seconds = time.time()
        normalized_key = rate_limit_key or UNKNOWN_CLIENT_IP
        hit_timestamps = self._hits.setdefault(normalized_key, deque())
        cutoff_timestamp = current_timestamp_seconds - self.window_seconds
        # Drop expired hits.
        while hit_timestamps and hit_timestamps[0] < cutoff_timestamp:
            hit_timestamps.popleft()
        if len(hit_timestamps) >= self.max_hits:
            raise HTTPException(status_code=429, detail=error_detail)
        hit_timestamps.append(current_timestamp_seconds)


_chat_minute_limiter = SlidingWindowRateLimiter(
    CHAT_RATE_LIMIT_MAX_PER_MINUTE,
    CHAT_RATE_LIMIT_WINDOW_SECONDS_PER_MINUTE,
)
_chat_day_limiter = SlidingWindowRateLimiter(
    CHAT_RATE_LIMIT_MAX_PER_DAY,
    CHAT_RATE_LIMIT_WINDOW_SECONDS_PER_DAY,
)
_key_login_ip_limiter = SlidingWindowRateLimiter(
    KEY_LOGIN_MAX_PER_IP,
    KEY_LOGIN_WINDOW_SECONDS,
)
_key_login_fingerprint_limiter = SlidingWindowRateLimiter(
    KEY_LOGIN_MAX_PER_FINGERPRINT,
    KEY_LOGIN_FINGERPRINT_WINDOW_SECONDS,
)


def create_session_token(
    key_record: _KeyRecord,
    *,
    jwt_module: _JwtModule = jwt,
    secret_key: str = settings.jwt_secret,
    session_ttl_seconds: int = settings.session_ttl_seconds,
) -> tuple[str, datetime]:
    """Issue a short-lived session token bound to the access-key expiry.

    Inputs:
        key_record: Record containing an id, expiry, and optional label.
        jwt_module: JWT implementation for encoding the token.
        secret_key: Secret used to sign the token.
        session_ttl_seconds: Maximum lifetime for the session token.

    Outputs:
        A tuple of (encoded_token, session_expiration_datetime).

    Edge cases:
        If the access key expires sooner than the session TTL, the token is capped
        to the access-key expiration.
    """
    key_identifier = getattr(key_record, "id", None)
    if not key_identifier:
        raise ValueError("key_record.id is required")
    key_expires_at = getattr(key_record, "expires_at", None)
    if not key_expires_at:
        raise ValueError("key_record.expires_at is required")

    access_key_expires_at = _ensure_utc_aware(key_expires_at)
    session_ttl = timedelta(seconds=session_ttl_seconds)
    session_expires_at = min(access_key_expires_at, _utc_now() + session_ttl)

    payload: dict[str, Any] = {
        JWT_SUBJECT_FIELD: str(key_identifier),
        JWT_TYPE_FIELD: JWT_TYPE_SESSION,
        JWT_EXPIRATION_FIELD: int(session_expires_at.timestamp()),
    }
    label = getattr(key_record, "label", None)
    if label:
        payload[JWT_LABEL_FIELD] = label

    token = jwt_module.encode(payload, secret_key, algorithm=JWT_ALGORITHM)
    return token, session_expires_at


async def get_current_session(request: Request) -> Session:
    """Validate a bearer token from headers or cookies and return a session.

    Inputs:
        request: The inbound FastAPI request.

    Outputs:
        A Session extracted from the validated JWT.

    Edge cases:
        Returns 401 for missing, invalid, or expired tokens.
    """
    authorization_header = request.headers.get(AUTHORIZATION_HEADER_NAME)
    token: Optional[str] = None

    if authorization_header:
        try:
            scheme, token_value = authorization_header.split(" ", 1)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_INVALID_TOKEN,
            )
        if scheme.lower() != BEARER_AUTH_SCHEME or not token_value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_INVALID_TOKEN,
            )
        token = token_value

    if not token and settings.session_cookie_enabled:
        token = request.cookies.get(settings.session_cookie_name)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MISSING_TOKEN,
        )

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_TOKEN_EXPIRED,
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_INVALID_TOKEN,
        )

    if payload.get(JWT_TYPE_FIELD) != JWT_TYPE_SESSION or not payload.get(JWT_SUBJECT_FIELD):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_INVALID_TOKEN,
        )

    return Session(
        key_id=str(payload[JWT_SUBJECT_FIELD]),
        label=payload.get(JWT_LABEL_FIELD),
    )


async def check_rate_limit_dependency(request: Request) -> None:
    """Apply chat request rate limits per IP address.

    Inputs:
        request: The inbound FastAPI request.

    Outputs:
        None. Raises an HTTPException when limits are exceeded.

    Edge cases:
        Requests without client metadata are grouped under a placeholder key.
    """
    client_ip_address = _extract_client_ip_address(request)
    _chat_minute_limiter.hit(client_ip_address, error_detail=ERROR_RATE_LIMIT_EXCEEDED)
    _chat_day_limiter.hit(client_ip_address, error_detail=ERROR_RATE_LIMIT_EXCEEDED)


def enforce_key_login_rate_limits(
    request: Request,
    *,
    key_fingerprint: Optional[str] = None,
) -> None:
    """Limit key-login attempts per IP and per fingerprint before bcrypt verification.

    Inputs:
        request: The inbound FastAPI request.
        key_fingerprint: Optional fingerprint associated with the login attempt.

    Outputs:
        None. Raises an HTTPException when limits are exceeded.

    Edge cases:
        When no fingerprint is provided, only the IP-based limiter applies.
    """
    client_ip_address = _extract_client_ip_address(request)
    _key_login_ip_limiter.hit(client_ip_address, error_detail=ERROR_RATE_LIMITED)
    if key_fingerprint:
        _key_login_fingerprint_limiter.hit(key_fingerprint, error_detail=ERROR_RATE_LIMITED)


def _extract_client_ip_address(request: Request) -> str:
    """Return the client IP address, or a placeholder when unavailable.

    Inputs:
        request: The inbound FastAPI request.

    Outputs:
        The IP address string.

    Edge cases:
        Missing client metadata returns a placeholder value.
    """
    if request.client:
        return request.client.host
    return UNKNOWN_CLIENT_IP
