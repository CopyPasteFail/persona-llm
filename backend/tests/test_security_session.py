from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import security
from api.mock import app as mock_app
from api.settings import settings


SESSION_EXPIRATION_MINUTES = 30
CHAT_ENDPOINT_PATH = "/chat"
CHAT_QUESTION_PAYLOAD = {"question": "hi"}
SESSION_COOKIE_ENABLED_SETTING = "SESSION_COOKIE_ENABLED"
SESSION_AUTHORIZATION_HEADER = "Authorization"
BEARER_TOKEN_PREFIX = "Bearer"
TEST_BASE_URL = "http://test"
EXPECTED_MISSING_TOKEN_ERROR = "missing_token"
TEST_KEY_LABEL = "test"


class _FakeKeyRecord:
    def __init__(self, key_identifier: str):
        self.id = key_identifier
        self.expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=SESSION_EXPIRATION_MINUTES
        )
        self.label: str | None = TEST_KEY_LABEL


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Builds an async HTTP client against the mock app so tests can issue requests."""
    transport = ASGITransport(app=cast(Any, mock_app))
    async with AsyncClient(transport=transport, base_url=TEST_BASE_URL) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_chat_accepts_authorization_header(client: AsyncClient) -> None:
    """Checks that bearer tokens in the Authorization header are accepted by /chat,
    expecting a 200 response with a non-empty answer payload.
    """
    session_token, _ = security.create_session_token(_FakeKeyRecord("header-key"))
    response = await client.post(
        CHAT_ENDPOINT_PATH,
        json=CHAT_QUESTION_PAYLOAD,
        headers={SESSION_AUTHORIZATION_HEADER: f"{BEARER_TOKEN_PREFIX} {session_token}"},
    )
    assert response.status_code == 200
    assert response.json()["answer"]


@pytest.mark.asyncio
async def test_chat_accepts_cookie_when_enabled(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checks that cookie-based session tokens are accepted when enabled by setting the
    session cookie, expecting a 200 response with a non-empty answer payload.
    """
    session_token, _ = security.create_session_token(_FakeKeyRecord("cookie-key"))
    monkeypatch.setattr(settings, SESSION_COOKIE_ENABLED_SETTING, True)
    client.cookies.set(settings.session_cookie_name, session_token)
    response = await client.post(CHAT_ENDPOINT_PATH, json=CHAT_QUESTION_PAYLOAD)
    assert response.status_code == 200
    assert response.json()["answer"]


@pytest.mark.asyncio
async def test_chat_missing_token_returns_401(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checks that missing tokens are rejected by disabling cookies and calling /chat,
    expecting a 401 response with a missing_token error detail.
    """
    monkeypatch.setattr(settings, SESSION_COOKIE_ENABLED_SETTING, False)
    response = await client.post(CHAT_ENDPOINT_PATH, json=CHAT_QUESTION_PAYLOAD)
    assert response.status_code == 401
    assert response.json()["detail"] == EXPECTED_MISSING_TOKEN_ERROR
