from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Sequence, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import retrieval, security
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

_DETERMINISTIC_CHUNKS: list[dict[str, Any]] = [
    {"id": "mock:1", "text": "deterministic mock chunk", "metadata": {}}
]


class _FakeKeyRecord:
    def __init__(self, key_identifier: str):
        self.id = key_identifier
        self.expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=SESSION_EXPIRATION_MINUTES
        )
        self.label: str | None = TEST_KEY_LABEL


class _DeterministicEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [1.0]


class _DeterministicVectorClient:
    def query(
        self, embedding: Sequence[float], *, top_k: int
    ) -> list[dict[str, Any]]:
        return [{"id": "mock:1", "distance": 0.0}]


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Builds an async HTTP client against the mock app so tests can issue requests."""
    retrieval.configure_embedding_client(_DeterministicEmbeddingClient())
    retrieval.configure_vector_client(_DeterministicVectorClient())
    retrieval.configure_chunk_store(_DETERMINISTIC_CHUNKS)
    transport = ASGITransport(app=cast(Any, mock_app))
    try:
        async with AsyncClient(transport=transport, base_url=TEST_BASE_URL) as async_client:
            yield async_client
    finally:
        retrieval.configure_embedding_client(None)
        retrieval.configure_vector_client(None)
        retrieval.configure_chunk_store(None)


@pytest.mark.asyncio
async def test_chat_accepts_authorization_header(client: AsyncClient) -> None:
    """Verify bearer tokens in Authorization headers are accepted.

    What is tested:
        /chat authentication using the Authorization header.
    How it's tested:
        Create a session token and call /chat with a Bearer header.
    Expected result format:
        Status is 200 and the response JSON includes a non-empty answer.
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
    """Verify session cookies are accepted when enabled.

    What is tested:
        /chat authentication using session cookies when enabled in settings.
    How it's tested:
        Enable cookie auth, set the session cookie, and call /chat.
    Expected result format:
        Status is 200 and the response JSON includes a non-empty answer.
    """
    session_token, _ = security.create_session_token(_FakeKeyRecord("cookie-key"))
    monkeypatch.setattr(settings, SESSION_COOKIE_ENABLED_SETTING, True)
    client.cookies.set(settings.session_cookie_name, session_token)
    response = await client.post(CHAT_ENDPOINT_PATH, json=CHAT_QUESTION_PAYLOAD)
    assert response.status_code == 200
    assert response.json()["answer"]


@pytest.mark.asyncio
async def test_chat_missing_token_returns_401(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify missing tokens return a 401 error.

    What is tested:
        /chat authentication behavior when no auth token is provided.
    How it's tested:
        Disable cookie auth and call /chat without headers or cookies.
    Expected result format:
        Status is 401 and response detail equals EXPECTED_MISSING_TOKEN_ERROR.
    """
    monkeypatch.setattr(settings, SESSION_COOKIE_ENABLED_SETTING, False)
    response = await client.post(CHAT_ENDPOINT_PATH, json=CHAT_QUESTION_PAYLOAD)
    assert response.status_code == 401
    assert response.json()["detail"] == EXPECTED_MISSING_TOKEN_ERROR
