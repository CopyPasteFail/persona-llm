"""Tests for persona voice normalization and response contract behavior."""

from typing import Any, AsyncGenerator, Protocol, Sequence, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response

from api.mock import app as mock_app
from api import retrieval

ACCESS_TOKEN_JSON_FIELD = "access_token"
AUTH_ENDPOINT = "/auth/key-login"
BEARER_TOKEN_PREFIX = "Bearer"
CHAT_ENDPOINT = "/chat"
EXPECTED_RESPONSE_KEYS = {"answer", "citations", "usage", "llm_called"}
FIRST_PERSON_PHRASES = [" I ", " my ", " me "]
HTTP_OK_STATUS = 200
TEST_BASE_URL = "http://test"
TEST_KEY = "persona-voice-key"
TEST_QUESTION = "What did John do with Kubernetes at Google in 2021?"


class AccessKeyStore(Protocol):
    def add_plain_key(self, plain_key: str) -> str: ...


class _DeterministicEmbeddingClient:
    """Deterministic embedding stub for mock mode."""

    def embed(self, text: str) -> list[float]:
        return [1.0]


class _DeterministicVectorClient:
    """Deterministic vector search stub for mock mode."""

    def query(self, embedding: Sequence[float], *, top_k: int) -> list[dict[str, Any]]:
        return [{"chunk_id": "mock:1", "distance": 0.0}]


@pytest_asyncio.fixture
async def test_client(
    access_key_store: AccessKeyStore,
) -> AsyncGenerator[AsyncClient, None]:
    """Verify the test client fixture yields an AsyncClient bound to the mock app.

    What is tested:
        Fixture setup for the mock app client and seeded access key.
    How it's tested:
        Seed the access key store and create an AsyncClient with ASGITransport.
    Expected result format:
        The fixture yields an AsyncClient ready for login and chat requests.
    """
    access_key_store.add_plain_key(TEST_KEY)
    retrieval.configure_embedding_client(_DeterministicEmbeddingClient())
    retrieval.configure_vector_client(_DeterministicVectorClient())
    retrieval.configure_chunk_store(
        [{"chunk_id": "mock:1", "text": "deterministic mock chunk"}]
    )
    transport = ASGITransport(app=cast(Any, mock_app))
    try:
        async with AsyncClient(
            transport=transport,
            base_url=TEST_BASE_URL,
        ) as http_client:
            yield http_client
    finally:
        retrieval.configure_embedding_client(None)
        retrieval.configure_vector_client(None)
        retrieval.configure_chunk_store(None)


@pytest.mark.asyncio
async def test_first_person_normalization(test_client: AsyncClient) -> None:
    """Verify mock responses normalize to first person.

    What is tested:
        Chat response contract and first-person normalization in mock app.
    How it's tested:
        Log in, call /chat with a third-person question, and inspect the answer.
    Expected result format:
        Response has answer/citations/usage, includes TLDR, and uses first-person.
    """
    login_response: Response = await test_client.post(
        AUTH_ENDPOINT,
        json={"key": TEST_KEY},
    )
    assert login_response.status_code == HTTP_OK_STATUS, login_response.text
    access_token: str = login_response.json()[ACCESS_TOKEN_JSON_FIELD]

    chat_response: Response = await test_client.post(
        CHAT_ENDPOINT,
        json={"question": TEST_QUESTION},
        headers={"Authorization": f"{BEARER_TOKEN_PREFIX} {access_token}"},
    )
    assert chat_response.status_code == HTTP_OK_STATUS, chat_response.text
    response_payload: dict[str, Any] = chat_response.json()

    # Contract checks
    assert EXPECTED_RESPONSE_KEYS.issubset(response_payload.keys())
    assert (
        isinstance(response_payload["answer"], str) and response_payload["answer"]
    )
    citations = cast(list[dict[str, Any]], response_payload["citations"])
    assert isinstance(citations, list) and len(citations) >= 1
    assert "id" in citations[0]
    assert isinstance(response_payload["usage"]["input_tokens"], int)
    assert isinstance(response_payload["usage"]["output_tokens"], int)
    assert isinstance(response_payload["llm_called"], bool)

    # Content checks
    answer_text: str = response_payload["answer"]
    assert "TLDR:" in answer_text
    assert "filter:" not in answer_text.lower()

    # First-person sanity: the mock returns first-person phrasing
    assert any(phrase in answer_text for phrase in FIRST_PERSON_PHRASES)
