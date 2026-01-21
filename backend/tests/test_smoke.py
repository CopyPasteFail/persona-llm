"""Smoke tests for the mock API endpoints and auth flow.

Scope: verify basic health, auth, and chat response contracts using the mock
app with deterministic retrieval stubs.
Key behaviors: endpoint availability, auth enforcement, and response shape.
Notes: the mock app still runs the full RAG pipeline (embed, vector search,
filtering, then LLM generation). The LLM answer is deterministic, but retrieval
still happens. In deterministic mode, embedding/vector are stubs that return
fixed values, and the chunk store uses _DETERMINISTIC_CHUNKS (not real data).
"""

from datetime import datetime
from typing import Any, AsyncGenerator, Protocol, Sequence, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.mock import app as mock_app
from api import retrieval

BASE_URL = "http://test"
HEALTH_ENDPOINT = "/health"
KEY_LOGIN_ENDPOINT = "/auth/key-login"
CHAT_ENDPOINT = "/chat"
SAMPLE_QUESTION = "Tell me about my Kubernetes experience and Ansible work."
TEST_ACCESS_KEY = "test-access-key-123"

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
TOKEN_TYPE_BEARER = "bearer"
EXPECTED_HEALTH_STATUS = "ok"

_DETERMINISTIC_CHUNKS: list[dict[str, Any]] = [
    {"id": "mock:1", "text": "deterministic mock chunk", "metadata": {}}
]


class _DeterministicEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [1.0]


class _DeterministicVectorClient:
    def query(
        self, embedding: Sequence[float], *, top_k: int
    ) -> list[dict[str, Any]]:
        return [{"id": "mock:1", "distance": 0.0}]


class AccessKeyStore(Protocol):
    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str: ...


@pytest_asyncio.fixture
async def client(
    access_key_store: AccessKeyStore,
) -> AsyncGenerator[AsyncClient, None]:
    """Builds an async client wired to the mock ASGI app with a seeded access key,
    so tests can exercise HTTP endpoints and expect authenticated calls to work.
    """
    retrieval.configure_embedding_client(_DeterministicEmbeddingClient())
    retrieval.configure_vector_client(_DeterministicVectorClient())
    retrieval.configure_chunk_store(_DETERMINISTIC_CHUNKS)
    access_key_store.add_plain_key(TEST_ACCESS_KEY, label="test")
    transport = ASGITransport(app=cast(Any, mock_app))
    try:
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield client
    finally:
        retrieval.configure_embedding_client(None)
        retrieval.configure_vector_client(None)
        retrieval.configure_chunk_store(None)


@pytest.mark.asyncio
async def test_health_ready(client: AsyncClient) -> None:
    """Verify /health reports OK.

    What is tested:
        /health response status and payload.
    How it's tested:
        Call the health endpoint with the test client.
    Expected result format:
        Status is 200 and JSON status equals EXPECTED_HEALTH_STATUS.
    """
    response = await client.get(HEALTH_ENDPOINT)
    assert response.status_code == HTTP_OK
    response_data: dict[str, Any] = response.json()
    assert response_data.get("status") == EXPECTED_HEALTH_STATUS


@pytest.mark.asyncio
async def test_key_login_returns_token(client: AsyncClient) -> None:
    """Verify /auth/key-login returns a bearer token.

    What is tested:
        /auth/key-login success response fields.
    How it's tested:
        Post TEST_ACCESS_KEY to the login endpoint and inspect JSON.
    Expected result format:
        Status is 200 with token_type, access_token, and expires_at present.
    """
    request_payload = {"key": TEST_ACCESS_KEY}
    response = await client.post(KEY_LOGIN_ENDPOINT, json=request_payload)
    assert response.status_code == HTTP_OK
    response_data: dict[str, Any] = response.json()
    assert response_data["token_type"] == TOKEN_TYPE_BEARER
    assert isinstance(response_data["access_token"], str)
    assert response_data["access_token"]
    assert "expires_at" in response_data


@pytest.mark.asyncio
async def test_chat_requires_auth(client: AsyncClient) -> None:
    """Verify /chat enforces authentication.

    What is tested:
        /chat access control when no credentials are provided.
    How it's tested:
        Call /chat without auth headers or cookies.
    Expected result format:
        Status is 401.
    """
    request_payload = {"question": SAMPLE_QUESTION}
    response = await client.post(CHAT_ENDPOINT, json=request_payload)
    assert response.status_code == HTTP_UNAUTHORIZED


@pytest.mark.asyncio
async def test_chat_basic(client: AsyncClient) -> None:
    """Verify /chat returns a valid response for an authenticated request.

    What is tested:
        /chat response contract and content sanity for a valid auth token.
    How it's tested:
        Log in to get a bearer token, then call /chat with that token.
    Expected result format:
        Status is 200 with answer/citations/usage fields populated and sane.
    """
    login_response = await client.post(
        KEY_LOGIN_ENDPOINT,
        json={"key": TEST_ACCESS_KEY},
    )
    access_token = login_response.json()["access_token"]

    request_payload = {"question": SAMPLE_QUESTION}
    response = await client.post(
        CHAT_ENDPOINT,
        json=request_payload,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == HTTP_OK
    response_data: dict[str, Any] = response.json()
    # Contract
    assert {"answer", "citations", "usage"}.issubset(response_data.keys())
    assert isinstance(response_data["answer"], str)
    assert response_data["answer"]
    assert isinstance(response_data["citations"], list)
    assert response_data["citations"]
    assert "id" in response_data["citations"][0]
    assert isinstance(response_data["usage"]["output_tokens"], int)
    assert response_data["usage"]["output_tokens"] > 0

    # Content sanity
    assert "TLDR:" in response_data["answer"]
    # No more filter lines in the output
    assert "filter:" not in response_data["answer"].lower()
