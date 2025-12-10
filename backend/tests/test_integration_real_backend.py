import os
import pytest
import httpx

ACCESS_KEY_ENV = "ACCESS_KEY_PLAINTEXT"

@pytest.fixture(scope="module")
def base_url():
    url = os.getenv("NEXT_PUBLIC_API_URL", "")
    print(f"[integration] Target BASE_URL={url!r}")
    if not url or not url.startswith("http"):
        pytest.skip("Set NEXT_PUBLIC_API_URL to run real backend integration tests")
    return url.rstrip("/")


@pytest.mark.integration
def test_real_backend_health(base_url):
    resp = httpx.get(f"{base_url}/health", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"


@pytest.mark.integration
def test_real_backend_first_person(base_url):
    """Integration test: requires uvicorn api.main:app running locally with real GCP creds/envs."""
    key = os.getenv(ACCESS_KEY_ENV)
    if not key:
        pytest.skip(f"Set {ACCESS_KEY_ENV} to a valid access key before running integration tests.")

    login = httpx.post(
        f"{base_url}/auth/key-login",
        json={"key": key},
        timeout=10,
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    resp = httpx.post(
        f"{base_url}/chat",
        headers={"Authorization": f"Bearer {token}"},
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
