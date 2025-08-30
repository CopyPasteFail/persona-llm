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
async def test_first_person_normalization(client):
    """
    The mock normalizes third-person mentions of 'Omer' to first person.
    This is a sanity check to ensure the normalization logic is working.
    """
    resp = await client.post(
        "/chat",
        json={"question": "What did Omer do with Kubernetes at Nexyte in 2024?"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Contract checks
    assert set(data.keys()) == {"answer", "citations", "usage"}
    assert isinstance(data["answer"], str) and data["answer"]
    assert isinstance(data["citations"], list) and len(data["citations"]) >= 1
    assert "id" in data["citations"][0]
    assert isinstance(data["usage"]["input_tokens"], int)
    assert isinstance(data["usage"]["output_tokens"], int)

    # Content checks
    ans = data["answer"]
    assert "TLDR:" in ans
    assert "filter:" not in ans.lower()

    # First-person sanity: the mock returns first-person phrasing
    assert any(p in ans for p in [" I ", " my ", " me "])
