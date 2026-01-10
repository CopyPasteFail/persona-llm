"""Tests for persona voice normalization and response contract behavior."""

from typing import Any, AsyncGenerator, Protocol, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response

from api.mock import app as mock_app

ACCESS_TOKEN_JSON_FIELD = "access_token"
AUTH_ENDPOINT = "/auth/key-login"
BEARER_TOKEN_PREFIX = "Bearer"
CHAT_ENDPOINT = "/chat"
EXPECTED_RESPONSE_KEYS = {"answer", "citations", "usage"}
FIRST_PERSON_PHRASES = [" I ", " my ", " me "]
HTTP_OK_STATUS = 200
TEST_BASE_URL = "http://test"
TEST_KEY = "persona-voice-key"
TEST_QUESTION = "What did John do with Kubernetes at Google in 2021?"


class AccessKeyStore(Protocol):
    def add_plain_key(self, plain_key: str) -> str: ...


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
    transport = ASGITransport(app=cast(Any, mock_app))
    async with AsyncClient(
        transport=transport,
        base_url=TEST_BASE_URL,
    ) as http_client:
        yield http_client


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
    assert set(response_payload.keys()) == EXPECTED_RESPONSE_KEYS
    assert (
        isinstance(response_payload["answer"], str) and response_payload["answer"]
    )
    citations = cast(list[dict[str, Any]], response_payload["citations"])
    assert isinstance(citations, list) and len(citations) >= 1
    assert "id" in citations[0]
    assert isinstance(response_payload["usage"]["input_tokens"], int)
    assert isinstance(response_payload["usage"]["output_tokens"], int)

    # Content checks
    answer_text: str = response_payload["answer"]
    assert "TLDR:" in answer_text
    assert "filter:" not in answer_text.lower()

    # First-person sanity: the mock returns first-person phrasing
    assert any(phrase in answer_text for phrase in FIRST_PERSON_PHRASES)
