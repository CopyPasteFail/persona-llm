from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import security
from api.mock import app as mock_app

TEST_KEY = "login-test-key"


@pytest.fixture(autouse=True)
def reset_key_login_limiters(monkeypatch):
    monkeypatch.setattr(
        security,
        "_key_login_ip_limiter",
        security.SlidingWindowRateLimiter(security.KEY_LOGIN_MAX_PER_IP, security.KEY_LOGIN_WINDOW_SECONDS),
    )
    monkeypatch.setattr(
        security,
        "_key_login_fingerprint_limiter",
        security.SlidingWindowRateLimiter(
            security.KEY_LOGIN_MAX_PER_FINGERPRINT, security.KEY_LOGIN_FINGERPRINT_WINDOW_SECONDS
        ),
    )


@pytest_asyncio.fixture
async def client(access_key_store):
    transport = ASGITransport(app=mock_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_key_login_invalid_key(client):
    resp = await client.post("/auth/key-login", json={"key": "unknown"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_key"


@pytest.mark.asyncio
async def test_key_login_expired_key(client, access_key_store):
    access_key_store.add_plain_key(
        TEST_KEY,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    resp = await client.post("/auth/key-login", json={"key": TEST_KEY})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "key_expired"


@pytest.mark.asyncio
async def test_key_login_revoked_key(client, access_key_store):
    access_key_store.add_plain_key(TEST_KEY, revoked=True)
    resp = await client.post("/auth/key-login", json={"key": TEST_KEY})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "key_revoked"


@pytest.mark.asyncio
async def test_key_login_success(client, access_key_store):
    access_key_store.add_plain_key(TEST_KEY, label="ok")
    resp = await client.post("/auth/key-login", json={"key": TEST_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert isinstance(data["access_token"], str) and data["access_token"]
    assert data["expires_at"]


@pytest.mark.asyncio
async def test_key_login_rate_limited(client, access_key_store, monkeypatch):
    access_key_store.add_plain_key(TEST_KEY)
    tight_ip_limiter = security.SlidingWindowRateLimiter(2, 60)
    tight_fp_limiter = security.SlidingWindowRateLimiter(2, 60)
    monkeypatch.setattr(security, "_key_login_ip_limiter", tight_ip_limiter)
    monkeypatch.setattr(security, "_key_login_fingerprint_limiter", tight_fp_limiter)

    for _ in range(2):
        ok = await client.post("/auth/key-login", json={"key": TEST_KEY})
        assert ok.status_code == 200

    resp = await client.post("/auth/key-login", json={"key": TEST_KEY})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


@pytest.mark.asyncio
async def test_key_login_sets_cookie_when_enabled(client, access_key_store, monkeypatch):
    access_key_store.add_plain_key(TEST_KEY, label="cookie")
    monkeypatch.setattr(security.settings, "SESSION_COOKIE_ENABLED", True)
    monkeypatch.setattr(security.settings, "SESSION_COOKIE_NAME", "session")
    monkeypatch.setattr(security.settings, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(security.settings, "SESSION_COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(security.settings, "SESSION_COOKIE_PATH", "/")

    resp = await client.post("/auth/key-login", json={"key": TEST_KEY})
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie")
    assert set_cookie is not None
    assert "session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "path=/" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "secure" in set_cookie.lower()
