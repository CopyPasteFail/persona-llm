import os
import pytest
import httpx

BASE_URL = os.getenv("REAL_BACKEND_URL", "http://localhost:8000")

@pytest.mark.integration
def test_real_backend_first_person():
    """Integration test: requires uvicorn api.main:app running locally with real GCP creds/envs."""
    resp = httpx.post(
        f"{BASE_URL}/chat",
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
