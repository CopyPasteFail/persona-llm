from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Protocol, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import security
from api.mock import app as mock_app

ACCESS_TOKEN_FIELD = "access_token"
AUTH_KEY_LOGIN_ENDPOINT = "/auth/key-login"
BEARER_TOKEN_TYPE = "bearer"
COOKIE_ATTRIBUTE_HTTPONLY = "httponly"
COOKIE_ATTRIBUTE_SECURE = "secure"
DETAIL_FIELD = "detail"
EXPIRED_KEY_DETAIL = "key_expired"
EXPIRED_KEY_MINUTES = 1
INVALID_KEY_DETAIL = "invalid_key"
KEY_FIELD = "key"
KEY_LOGIN_FINGERPRINT_LIMITER_ATTR = "_key_login_fingerprint_limiter"
KEY_LOGIN_IP_LIMITER_ATTR = "_key_login_ip_limiter"
KEY_LOGIN_TEST_KEY = "login-test-key"
RATE_LIMITED_DETAIL = "rate_limited"
RESPONSE_FIELD_EXPIRES_AT = "expires_at"
RESPONSE_FIELD_TOKEN_TYPE = "token_type"
REVOKED_KEY_DETAIL = "key_revoked"
SESSION_COOKIE_HEADER = "set-cookie"
SESSION_COOKIE_NAME = "session"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_SAMESITE = "lax"
SESSION_LABEL = "cookie"
SETTINGS_SESSION_COOKIE_ENABLED_ATTR = "SESSION_COOKIE_ENABLED"
SETTINGS_SESSION_COOKIE_NAME_ATTR = "SESSION_COOKIE_NAME"
SETTINGS_SESSION_COOKIE_PATH_ATTR = "SESSION_COOKIE_PATH"
SETTINGS_SESSION_COOKIE_SAMESITE_ATTR = "SESSION_COOKIE_SAMESITE"
SETTINGS_SESSION_COOKIE_SECURE_ATTR = "SESSION_COOKIE_SECURE"
SUCCESS_KEY_LABEL = "ok"
TEST_BASE_URL = "http://test"
TIGHT_RATE_LIMIT_MAX_ATTEMPTS = 2
TIGHT_RATE_LIMIT_WINDOW_SECONDS = 60
UNKNOWN_KEY = "unknown"
HTTP_STATUS_OK = 200
HTTP_STATUS_TOO_MANY_REQUESTS = 429
HTTP_STATUS_UNAUTHORIZED = 401


class AccessKeyStore(Protocol):
    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str: ...


@pytest.fixture(autouse=True)
def reset_key_login_limiters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resets the key-login rate limiters before each test by reinitializing
    limiter instances so tests run with known defaults.
    """
    monkeypatch.setattr(
        security,
        KEY_LOGIN_IP_LIMITER_ATTR,
        security.SlidingWindowRateLimiter(
            security.KEY_LOGIN_MAX_PER_IP,
            security.KEY_LOGIN_WINDOW_SECONDS,
        ),
    )
    monkeypatch.setattr(
        security,
        KEY_LOGIN_FINGERPRINT_LIMITER_ATTR,
        security.SlidingWindowRateLimiter(
            security.KEY_LOGIN_MAX_PER_FINGERPRINT,
            security.KEY_LOGIN_FINGERPRINT_WINDOW_SECONDS,
        ),
    )


@pytest_asyncio.fixture
async def client(access_key_store: AccessKeyStore) -> AsyncGenerator[AsyncClient, None]:
    """Builds an async HTTP client for the mock app so tests can perform requests
    against the key-login endpoint and validate responses.
    """
    transport = ASGITransport(app=cast(Any, mock_app))
    async with AsyncClient(transport=transport, base_url=TEST_BASE_URL) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_key_login_invalid_key(client: AsyncClient) -> None:
    """Verify unknown keys are rejected with an invalid_key detail.

    What is tested:
        Key-login endpoint behavior when the key is not in the store.
    How it's tested:
        Post UNKNOWN_KEY to /auth/key-login and inspect the response JSON.
    Expected result format:
        Status is 401 and the response detail equals INVALID_KEY_DETAIL.
    """
    response = await client.post(AUTH_KEY_LOGIN_ENDPOINT, json={KEY_FIELD: UNKNOWN_KEY})
    assert response.status_code == HTTP_STATUS_UNAUTHORIZED
    response_data: dict[str, Any] = response.json()
    assert response_data[DETAIL_FIELD] == INVALID_KEY_DETAIL


@pytest.mark.asyncio
async def test_key_login_expired_key(client: AsyncClient, access_key_store: AccessKeyStore) -> None:
    """Verify expired keys are rejected with a key_expired detail.

    What is tested:
        Key-login behavior when a key is expired.
    How it's tested:
        Seed an expired key and post it to /auth/key-login.
    Expected result format:
        Status is 401 and the response detail equals EXPIRED_KEY_DETAIL.
    """
    access_key_store.add_plain_key(
        KEY_LOGIN_TEST_KEY,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=EXPIRED_KEY_MINUTES),
    )
    response = await client.post(AUTH_KEY_LOGIN_ENDPOINT, json={KEY_FIELD: KEY_LOGIN_TEST_KEY})
    assert response.status_code == HTTP_STATUS_UNAUTHORIZED
    response_data: dict[str, Any] = response.json()
    assert response_data[DETAIL_FIELD] == EXPIRED_KEY_DETAIL


@pytest.mark.asyncio
async def test_key_login_revoked_key(client: AsyncClient, access_key_store: AccessKeyStore) -> None:
    """Verify revoked keys are rejected with a key_revoked detail.

    What is tested:
        Key-login behavior when a key is revoked.
    How it's tested:
        Seed a revoked key and post it to /auth/key-login.
    Expected result format:
        Status is 401 and the response detail equals REVOKED_KEY_DETAIL.
    """
    access_key_store.add_plain_key(KEY_LOGIN_TEST_KEY, revoked=True)
    response = await client.post(AUTH_KEY_LOGIN_ENDPOINT, json={KEY_FIELD: KEY_LOGIN_TEST_KEY})
    assert response.status_code == HTTP_STATUS_UNAUTHORIZED
    response_data: dict[str, Any] = response.json()
    assert response_data[DETAIL_FIELD] == REVOKED_KEY_DETAIL


@pytest.mark.asyncio
async def test_key_login_success(client: AsyncClient, access_key_store: AccessKeyStore) -> None:
    """Verify valid keys return a bearer token payload.

    What is tested:
        Successful key-login response shape and token metadata.
    How it's tested:
        Seed a valid key and post it to /auth/key-login.
    Expected result format:
        Status is 200 with token_type, access_token, and expires_at fields.
    """
    access_key_store.add_plain_key(KEY_LOGIN_TEST_KEY, label=SUCCESS_KEY_LABEL)
    response = await client.post(AUTH_KEY_LOGIN_ENDPOINT, json={KEY_FIELD: KEY_LOGIN_TEST_KEY})
    assert response.status_code == HTTP_STATUS_OK
    response_data: dict[str, Any] = response.json()
    assert response_data[RESPONSE_FIELD_TOKEN_TYPE] == BEARER_TOKEN_TYPE
    assert isinstance(response_data[ACCESS_TOKEN_FIELD], str) and response_data[ACCESS_TOKEN_FIELD]
    assert response_data[RESPONSE_FIELD_EXPIRES_AT]


@pytest.mark.asyncio
async def test_key_login_rate_limited(
    client: AsyncClient,
    access_key_store: AccessKeyStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify rate limits return a rate_limited detail after max attempts.

    What is tested:
        Key-login rate limiting across IP and fingerprint limiters.
    How it's tested:
        Tighten limiters, perform max allowed logins, then attempt one more.
    Expected result format:
        Status is 429 and the response detail equals RATE_LIMITED_DETAIL.
    """
    access_key_store.add_plain_key(KEY_LOGIN_TEST_KEY)
    tight_ip_limiter = security.SlidingWindowRateLimiter(
        TIGHT_RATE_LIMIT_MAX_ATTEMPTS,
        TIGHT_RATE_LIMIT_WINDOW_SECONDS,
    )
    tight_fingerprint_limiter = security.SlidingWindowRateLimiter(
        TIGHT_RATE_LIMIT_MAX_ATTEMPTS,
        TIGHT_RATE_LIMIT_WINDOW_SECONDS,
    )
    monkeypatch.setattr(security, KEY_LOGIN_IP_LIMITER_ATTR, tight_ip_limiter)
    monkeypatch.setattr(security, KEY_LOGIN_FINGERPRINT_LIMITER_ATTR, tight_fingerprint_limiter)

    for _ in range(TIGHT_RATE_LIMIT_MAX_ATTEMPTS):
        success_response = await client.post(
            AUTH_KEY_LOGIN_ENDPOINT,
            json={KEY_FIELD: KEY_LOGIN_TEST_KEY},
        )
        assert success_response.status_code == HTTP_STATUS_OK

    response = await client.post(AUTH_KEY_LOGIN_ENDPOINT, json={KEY_FIELD: KEY_LOGIN_TEST_KEY})
    assert response.status_code == HTTP_STATUS_TOO_MANY_REQUESTS
    response_data: dict[str, Any] = response.json()
    assert response_data[DETAIL_FIELD] == RATE_LIMITED_DETAIL


@pytest.mark.asyncio
async def test_key_login_sets_cookie_when_enabled(
    client: AsyncClient,
    access_key_store: AccessKeyStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify successful logins set a session cookie when enabled.

    What is tested:
        Cookie-based session issuance behavior from key login.
    How it's tested:
        Enable session cookie settings and post a valid key.
    Expected result format:
        Status is 200 and Set-Cookie includes name, path, samesite, and secure.
    """
    access_key_store.add_plain_key(KEY_LOGIN_TEST_KEY, label=SESSION_LABEL)
    monkeypatch.setattr(security.settings, SETTINGS_SESSION_COOKIE_ENABLED_ATTR, True)
    monkeypatch.setattr(security.settings, SETTINGS_SESSION_COOKIE_NAME_ATTR, SESSION_COOKIE_NAME)
    monkeypatch.setattr(security.settings, SETTINGS_SESSION_COOKIE_SECURE_ATTR, True)
    monkeypatch.setattr(security.settings, SETTINGS_SESSION_COOKIE_SAMESITE_ATTR, SESSION_COOKIE_SAMESITE)
    monkeypatch.setattr(security.settings, SETTINGS_SESSION_COOKIE_PATH_ATTR, SESSION_COOKIE_PATH)

    response = await client.post(AUTH_KEY_LOGIN_ENDPOINT, json={KEY_FIELD: KEY_LOGIN_TEST_KEY})
    assert response.status_code == HTTP_STATUS_OK
    set_cookie_header = response.headers.get(SESSION_COOKIE_HEADER)
    assert set_cookie_header is not None
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie_header
    assert COOKIE_ATTRIBUTE_HTTPONLY in set_cookie_header.lower()
    assert f"path={SESSION_COOKIE_PATH}" in set_cookie_header.lower()
    assert f"samesite={SESSION_COOKIE_SAMESITE}" in set_cookie_header.lower()
    assert COOKIE_ATTRIBUTE_SECURE in set_cookie_header.lower()
