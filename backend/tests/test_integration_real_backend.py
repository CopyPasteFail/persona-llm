"""Integration tests that hit an integrated backend using Firestore credentials and config."""

import os
from typing import Any
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
HTTP_SERVICE_UNAVAILABLE_STATUS = 503
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
CHAT_RATE_LIMIT_DETAIL = "rate limit exceeded"
KEY_LOGIN_MAX_ATTEMPTS_PER_FINGERPRINT = 5
CHAT_RATE_LIMIT_MAX_PER_MINUTE = 10
CHAT_RATE_LIMIT_ATTEMPT_BUFFER = 5
DUMMY_ACCESS_KEY = "rate-limit-dummy-key"
GATING_QUERY_CASES: tuple[tuple[str, str], ...] = (
    ("hi", "Hi, happy to chat."),
    ("good morning", "Hi, happy to chat."),
    ("מה הניסיון שלך בדבאופס?", "TLDR: I support English input only right now."),
    ("How many years of experience?", "TLDR: I have about"),
    ("How much of experience do you have in CI/CD?", "TLDR: I have about"),
    ("How many years of experience do you have in CI/CD?", "TLDR: I have about"),
    ("How many years of experience do you have with WordPress?", "TLDR: I have about"),
    ("How many years of experience do you have with C++?", "TLDR: I have about"),
    ("How long have you worked in DevOps?", "TLDR: I have about"),
)
NON_GATED_LLM_QUERY = "What did you do with Kubernetes at Cognyte?"
pytestmark = pytest.mark.integration

def _get_base_url() -> str:
    base_url = os.getenv(BASE_URL_ENV, "")
    print(f"[integration] Target BASE_URL={base_url!r}")
    if not base_url or not base_url.startswith(BASE_URL_PREFIX):
        pytest.skip(f"Set {BASE_URL_ENV} to run integration tests against an integrated backend ")
    return base_url.rstrip("/")


def _get_access_key() -> str:
    access_key = os.getenv(ACCESS_KEY_ENV)
    if not access_key:
        print(
            f"[integration] Missing {ACCESS_KEY_ENV}; "
            f"run: export {ACCESS_KEY_ENV}='your-access-key'"
        )
        pytest.skip(
            f"Set {ACCESS_KEY_ENV} to a valid access key before running integration tests. "
            f"Example: export {ACCESS_KEY_ENV}='your-access-key'"
        )
    return access_key


def _login_for_token(http_client: httpx.Client, base_url: str) -> str:
    access_key = _get_access_key()
    login_response = http_client.post(
        f"{base_url}{KEY_LOGIN_PATH}",
        json={"key": access_key},
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    assert login_response.status_code == HTTP_OK_STATUS, login_response.text
    return login_response.json()[ACCESS_TOKEN_FIELD]


def _send_chat_question(
    http_client: httpx.Client,
    *,
    base_url: str,
    token: str,
    question: str,
) -> dict[str, Any]:
    """Send a chat request and return the parsed response payload.

    Inputs:
        http_client: Shared integration HTTP client.
        base_url: Integrated backend base URL.
        token: Bearer token from key-login.
        question: User-facing chat question to send.

    Outputs:
        Parsed JSON response payload from /chat.

    Edge cases:
        Fails with assertion context when status is not 200.
    """
    chat_response = http_client.post(
        f"{base_url}{CHAT_PATH}",
        headers={AUTH_HEADER_NAME: f"Bearer {token}"},
        json={"question": question},
        timeout=CHAT_TIMEOUT_SECONDS,
    )
    assert chat_response.status_code == HTTP_OK_STATUS, chat_response.text
    return chat_response.json()


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
    """Verify integrated backend answers in first person.

    What is tested:
        Auth flow and chat response content against a live backend.
    How it's tested:
        Log in with a Firestore-hosted access key, then call /chat and inspect the answer.
    Expected result format:
        Statuses are 200, answer includes TLDR/Wrap markers and first-person.
    """
    token = _login_for_token(http_client, base_url)

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
    # In integrated mode, response should include first-person pronouns.
    assert any(pronoun in answer_text for pronoun in FIRST_PERSON_PRONOUNS)


@pytest.mark.integration
@pytest.mark.parametrize(("question_text", "expected_answer_marker"), GATING_QUERY_CASES)
def test_real_backend_chat_gated_queries_bypass_llm(
    base_url: str,
    http_client: httpx.Client,
    question_text: str,
    expected_answer_marker: str,
) -> None:
    """Verify deterministic gate queries bypass the LLM in integrated mode.

    What is tested:
        Greeting, non-English, and duration-intent prompts that must route through
        deterministic no-LLM paths.
    How it's tested:
        Log in once per test case, call /chat with each gating query, and assert
        response fields without relying on internal gate-reason logs.
    Expected result format:
        Status is 200, llm_called is false, citations are empty, and answer text
        includes the deterministic marker for that gate path.
    """
    token = _login_for_token(http_client, base_url)
    response_body = _send_chat_question(
        http_client,
        base_url=base_url,
        token=token,
        question=question_text,
    )

    assert response_body.get("llm_called") is False
    assert response_body.get("citations") == []
    answer_text = str(response_body.get(ANSWER_FIELD, ""))
    assert expected_answer_marker in answer_text


@pytest.mark.integration
def test_real_backend_chat_non_gated_query_calls_llm(
    base_url: str,
    http_client: httpx.Client,
) -> None:
    """Verify a normal in-domain query still calls the LLM in integrated mode.

    What is tested:
        A regular knowledge question that should use retrieval + LLM rather than
        deterministic gate bypass paths.
    How it's tested:
        Call /chat with a Kubernetes/Cognyte question and validate response shape.
    Expected result format:
        Status is 200, llm_called is true, answer has TLDR/Wrap markers, and
        citations are present.
    """
    token = _login_for_token(http_client, base_url)
    response_body = _send_chat_question(
        http_client,
        base_url=base_url,
        token=token,
        question=NON_GATED_LLM_QUERY,
    )

    assert response_body.get("llm_called") is True
    answer_text = str(response_body.get(ANSWER_FIELD, ""))
    assert TLDR_MARKER in answer_text and WRAP_MARKER in answer_text
    citations = response_body.get("citations")
    assert isinstance(citations, list) and len(citations) > 0


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


@pytest.mark.integration
def test_real_backend_chat_rate_limit_allows_503_until_limited(
    base_url: str,
    http_client: httpx.Client,
) -> None:
    """Verify /chat rate limits even if the backend returns 503s pre-limit."""
    token = _login_for_token(http_client, base_url)
    saw_rate_limit = False
    max_attempts = CHAT_RATE_LIMIT_MAX_PER_MINUTE + CHAT_RATE_LIMIT_ATTEMPT_BUFFER
    for _ in range(max_attempts):
        response = http_client.post(
            f"{base_url}{CHAT_PATH}",
            headers={AUTH_HEADER_NAME: f"Bearer {token}"},
            json={"question": "Ping?"},
            timeout=CHAT_TIMEOUT_SECONDS,
        )
        if response.status_code == HTTP_RATE_LIMIT_STATUS:
            assert response.json().get("detail") == CHAT_RATE_LIMIT_DETAIL
            saw_rate_limit = True
            break
        assert response.status_code in (
            HTTP_OK_STATUS,
            HTTP_SERVICE_UNAVAILABLE_STATUS,
        ), response.text

    assert saw_rate_limit, "Expected /chat rate limit to trigger after retries."
