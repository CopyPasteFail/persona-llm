import os
import pytest
import httpx

@pytest.fixture(scope="module")
def base_url():
    url = os.getenv("NEXT_PUBLIC_API_URL", "")
    print(f"[integration] Target BASE_URL={url!r}")
    assert url.startswith("http"), "NEXT_PUBLIC_API_URL must include protocol (e.g. https://...)"
    return url.rstrip("/")


@pytest.mark.integration
def test_real_backend_health(base_url):
    resp = httpx.get(f"{base_url}/health", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ready") is True


@pytest.mark.integration
def test_real_backend_first_person(base_url):
    """Integration test: requires uvicorn api.main:app running locally with real GCP creds/envs."""
    resp = httpx.post(
        f"{base_url}/chat",
        headers={"x-api-key": os.getenv("API_KEY", "test")},
        json={
            "question": "What did Omer do with Kubernetes at Nexyte in 2024?"
        },
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ans = data["answer"]
    assert "TLDR:" in ans and "Wrap:" in ans
    # In real mode, response should include first-person pronouns.
    assert any(p in ans for p in [" I ", " my ", " me "])
