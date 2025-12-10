from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import security
from api.mock import app as mock_app
from api.settings import settings


class _FakeKeyRecord:
    def __init__(self, key_id: str):
        self.id = key_id
        self.expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        self.label = "test"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=mock_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_chat_accepts_authorization_header(client):
    token, _ = security.create_session_token(_FakeKeyRecord("header-key"))
    resp = await client.post("/chat", json={"question": "hi"}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["answer"]


@pytest.mark.asyncio
async def test_chat_accepts_cookie_when_enabled(client, monkeypatch):
    token, _ = security.create_session_token(_FakeKeyRecord("cookie-key"))
    monkeypatch.setattr(settings, "SESSION_COOKIE_ENABLED", True)
    client.cookies.set(settings.session_cookie_name, token)
    resp = await client.post("/chat", json={"question": "hi"})
    assert resp.status_code == 200
    assert resp.json()["answer"]


@pytest.mark.asyncio
async def test_chat_missing_token_returns_401(client, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_COOKIE_ENABLED", False)
    resp = await client.post("/chat", json={"question": "hi"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_token"
