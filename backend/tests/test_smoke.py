import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from api.mock import app as mock_app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=mock_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_ready(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ready") is True


@pytest.mark.asyncio
async def test_chat_basic(client):
    payload = {
        "question": "Tell me about my Kubernetes experience and Ansible work."
    }
    resp = await client.post("/chat", json=payload)
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
