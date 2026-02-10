"""Integration tests that hit a live backend using real credentials and config."""

import os
from typing import Generator

import httpx
import pytest

ACCESS_KEY_ENV = "ACCESS_KEY_PLAINTEXT"
BASE_URL_ENV = "NEXT_PUBLIC_API_URL"
BASE_URL_PREFIX = "http"
HEALTH_PATH = "/health"
KEY_LOGIN_PATH = "/auth/key-login"
CHAT_PATH = "/chat"
HEALTH_TIMEOUT_SECONDS = 10
CHAT_TIMEOUT_SECONDS = 30
HTTP_OK_STATUS = 200
HTTP_UNAUTHORIZED_STATUS = 401
HTTP_RATE_LIMIT_STATUS = 429
STATUS_FIELD = "status"
ACCESS_TOKEN_FIELD = "access_token"
ANSWER_FIELD = "answer"
EXPECTED_STATUS = "ok"
QUESTION_TEXT = "What did he do with Kubernetes in 2024?"
AUTH_HEADER_NAME = "Authorization"
TLDR_MARKER = "TLDR:"
WRAP_MARKER = "Wrap:"
FIRST_PERSON_PRONOUNS = [" I ", " my ", " me "]
RATE_LIMIT_DETAIL = "rate_limited"
KEY_LOGIN_MAX_ATTEMPTS_PER_FINGERPRINT = 5
DUMMY_ACCESS_KEY = "rate-limit-dummy-key"

def _get_base_url() -> str:
    base_url = os.getenv(BASE_URL_ENV, "")
    print(f"[integration] Target BASE_URL={base_url!r}")
    if not base_url or not base_url.startswith(BASE_URL_PREFIX):
        pytest.skip(f"Set {BASE_URL_ENV} to run real backend integration tests")
    return base_url.rstrip("/")


def _get_access_key() -> str:
    access_key = os.getenv(ACCESS_KEY_ENV)
    if not access_key:
        pytest.skip(
            f"Set {ACCESS_KEY_ENV} to a valid access key before running integration tests."
        )
    return access_key


@pytest.fixture(scope="module")
def base_url() -> str:
    return _get_base_url()


@pytest.fixture(scope="module")
def http_client() -> Generator[httpx.Client, None, None]:
    client = httpx.Client()
    try:
        yield client
    finally:
        client.close()


@pytest.mark.integration
def test_real_backend_health(base_url: str, http_client: httpx.Client) -> None:
    """Verify the live health endpoint returns ok.

    What is tested:
        /health response status and payload.
    How it's tested:
        GET the health endpoint on the configured base URL.
    Expected result format:
        Status is 200 and JSON contains status == EXPECTED_STATUS.
    """
    response = http_client.get(
        f"{base_url}{HEALTH_PATH}",
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    assert response.status_code == HTTP_OK_STATUS
    response_body = response.json()
    assert response_body.get(STATUS_FIELD) == EXPECTED_STATUS


@pytest.mark.integration
def test_real_backend_first_person(base_url: str, http_client: httpx.Client) -> None:
    """Verify real backend answers in first person.

    What is tested:
        Auth flow and chat response content against a live backend.
    How it's tested:
        Log in with a real access key, then call /chat and inspect the answer.
    Expected result format:
        Statuses are 200, answer includes TLDR/Wrap markers and first-person.
    """
    access_key = _get_access_key()

    login_response = http_client.post(
        f"{base_url}{KEY_LOGIN_PATH}",
        json={"key": access_key},
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    assert login_response.status_code == HTTP_OK_STATUS, login_response.text
    token = login_response.json()[ACCESS_TOKEN_FIELD]

    chat_response = http_client.post(
        f"{base_url}{CHAT_PATH}",
        headers={AUTH_HEADER_NAME: f"Bearer {token}"},
        json={"question": QUESTION_TEXT},
        timeout=CHAT_TIMEOUT_SECONDS,
    )
    assert chat_response.status_code == HTTP_OK_STATUS, chat_response.text
    response_body = chat_response.json()
    answer_text = response_body[ANSWER_FIELD]
    assert TLDR_MARKER in answer_text and WRAP_MARKER in answer_text
    # In real mode, response should include first-person pronouns.
    assert any(pronoun in answer_text for pronoun in FIRST_PERSON_PRONOUNS)


@pytest.mark.integration
def test_real_backend_key_login_rate_limit(base_url: str, http_client: httpx.Client) -> None:
    """Verify live key-login rate limiting triggers for a single access key."""
    saw_rate_limit = False
    for _ in range(KEY_LOGIN_MAX_ATTEMPTS_PER_FINGERPRINT + 1):
        response = http_client.post(
            f"{base_url}{KEY_LOGIN_PATH}",
            json={"key": DUMMY_ACCESS_KEY},
            timeout=HEALTH_TIMEOUT_SECONDS,
        )
        if response.status_code == HTTP_RATE_LIMIT_STATUS:
            assert response.json().get("detail") == RATE_LIMIT_DETAIL
            saw_rate_limit = True
            break
        assert response.status_code in (HTTP_OK_STATUS, HTTP_UNAUTHORIZED_STATUS), response.text

    assert saw_rate_limit, "Expected live key-login rate limit to trigger."
