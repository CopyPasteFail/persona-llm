import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.mock import app as mock_app

TEST_ACCESS_KEY = "test-access-key-123"


@pytest_asyncio.fixture
async def client(access_key_store):
    access_key_store.add_plain_key(TEST_ACCESS_KEY, label="test")
    transport = ASGITransport(app=mock_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_ready(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"


@pytest.mark.asyncio
async def test_key_login_returns_token(client):
    payload = {"key": TEST_ACCESS_KEY}
    resp = await client.post("/auth/key-login", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert isinstance(data["access_token"], str) and data["access_token"]
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_chat_requires_auth(client):
    payload = {"question": "Tell me about my Kubernetes experience and Ansible work."}
    resp = await client.post("/chat", json=payload)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_basic(client):
    login = await client.post("/auth/key-login", json={"key": TEST_ACCESS_KEY})
    token = login.json()["access_token"]

    payload = {"question": "Tell me about my Kubernetes experience and Ansible work."}
    resp = await client.post(
        "/chat",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Contract
    assert set(data.keys()) == {"answer", "citations", "usage"}
    assert isinstance(data["answer"], str) and data["answer"]
    assert isinstance(data["citations"], list) and data["citations"]
    assert "id" in data["citations"][0]
    assert isinstance(data["usage"]["output_tokens"], int) and data["usage"]["output_tokens"] > 0

    # Content sanity
    assert "TLDR:" in data["answer"]
    # No more filter lines in the output
    assert "filter:" not in data["answer"].lower()
