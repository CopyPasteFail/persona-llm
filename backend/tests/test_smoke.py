from datetime import datetime
from typing import Any, AsyncGenerator, Protocol, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.mock import app as mock_app

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
    access_key_store.add_plain_key(TEST_ACCESS_KEY, label="test")
    transport = ASGITransport(app=cast(Any, mock_app))
    async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.mark.asyncio
async def test_health_ready(client: AsyncClient) -> None:
    """Calls the health endpoint to verify the service is reachable and reports
    an OK status payload; expects a 200 response and "ok" status.
    """
    response = await client.get(HEALTH_ENDPOINT)
    assert response.status_code == HTTP_OK
    response_data: dict[str, Any] = response.json()
    assert response_data.get("status") == EXPECTED_HEALTH_STATUS


@pytest.mark.asyncio
async def test_key_login_returns_token(client: AsyncClient) -> None:
    """Posts a valid access key to the login endpoint to ensure token issuance
    succeeds; expects HTTP 200 with bearer token data and expiration metadata.
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
    """Sends a chat request without auth headers to confirm access control
    is enforced; expects HTTP 401 unauthorized.
    """
    request_payload = {"question": SAMPLE_QUESTION}
    response = await client.post(CHAT_ENDPOINT, json=request_payload)
    assert response.status_code == HTTP_UNAUTHORIZED


@pytest.mark.asyncio
async def test_chat_basic(client: AsyncClient) -> None:
    """Logs in to obtain a bearer token, then calls the chat endpoint with auth
    to validate response shape and content sanity; expects HTTP 200 with
    answer, citations, and usage fields populated.
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
