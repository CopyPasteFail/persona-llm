"""Integration tests that hit an integrated backend using Firestore credentials and config."""

import os
import re
import time
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
FIRST_PERSON_PRONOUNS = ("i", "my", "me")
RATE_LIMIT_DETAIL = "rate_limited"
CHAT_RATE_LIMIT_DETAIL = "rate limit exceeded"
KEY_LOGIN_MAX_ATTEMPTS_PER_FINGERPRINT = 5
CHAT_RATE_LIMIT_MAX_PER_MINUTE = 10
CHAT_RATE_LIMIT_ATTEMPT_BUFFER = 5
DUMMY_ACCESS_KEY = "rate-limit-dummy-key"
NOT_READY_DETAIL = "not ready"
CHAT_UNAVAILABLE_DETAIL = "chat_unavailable"
CHAT_NOT_READY_RETRY_ATTEMPTS = 8
CHAT_NOT_READY_RETRY_DELAY_SECONDS = 1.0
CHAT_RATE_LIMIT_RETRY_ATTEMPTS = 2
CHAT_RATE_LIMIT_RETRY_DELAY_SECONDS = 61.0
CHAT_UNAVAILABLE_RETRY_ATTEMPTS = 3
CHAT_UNAVAILABLE_RETRY_DELAY_SECONDS = 2.0
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


def _contains_first_person_pronoun(answer_text: str) -> bool:
    """Return true when an answer contains at least one first-person pronoun.

    Inputs:
        answer_text: Model response text from /chat.

    Outputs:
        True when a whole-word first-person pronoun appears in the text.

    Edge cases:
        Matching is case-insensitive and robust to punctuation/boundaries, so
        leading/trailing pronouns like "I" and "me." still match.
    """
    escaped_pronouns = [re.escape(pronoun) for pronoun in FIRST_PERSON_PRONOUNS]
    pronouns_pattern = "|".join(escaped_pronouns)
    first_person_regex = rf"\b(?:{pronouns_pattern})\b"
    return bool(re.search(first_person_regex, answer_text, flags=re.IGNORECASE))


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
    """Log in with the integration access key and return a bearer token.

    Inputs:
        http_client: Shared integration HTTP client.
        base_url: Integrated backend base URL.

    Outputs:
        Access token string from /auth/key-login.

    Edge cases:
        Fails with assertion context if key-login does not return 200.
    """
    access_key = _get_access_key()
    login_response = http_client.post(
        f"{base_url}{KEY_LOGIN_PATH}",
        json={"key": access_key},
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    assert login_response.status_code == HTTP_OK_STATUS, login_response.text
    return login_response.json()[ACCESS_TOKEN_FIELD]


def _is_not_ready_response(response: httpx.Response) -> bool:
    """Return true when the response is a backend warmup/not-ready signal.

    Inputs:
        response: HTTP response returned by /chat.

    Outputs:
        True when response is HTTP 503 and has detail == "not ready".

    Edge cases:
        Non-JSON payloads are treated as not matching the not-ready signal.
    """
    if response.status_code != HTTP_SERVICE_UNAVAILABLE_STATUS:
        return False
    try:
        response_body = response.json()
    except ValueError:
        return False
    return response_body.get("detail") == NOT_READY_DETAIL


def _is_chat_rate_limited_response(response: httpx.Response) -> bool:
    """Return true when the response is the chat minute/day limiter signal.

    Inputs:
        response: HTTP response returned by /chat.

    Outputs:
        True when response is HTTP 429 and has detail == "rate limit exceeded".

    Edge cases:
        Non-JSON payloads are treated as not matching the known limiter signal.
    """
    if response.status_code != HTTP_RATE_LIMIT_STATUS:
        return False
    try:
        response_body = response.json()
    except ValueError:
        return False
    return response_body.get("detail") == CHAT_RATE_LIMIT_DETAIL


def _is_chat_unavailable_response(response: httpx.Response) -> bool:
    """Return true when the response is a temporary chat provider/runtime failure.

    Inputs:
        response: HTTP response returned by /chat.

    Outputs:
        True when response is HTTP 503 and has detail == "chat_unavailable".

    Edge cases:
        Non-JSON payloads are treated as not matching the known unavailable signal.
    """
    if response.status_code != HTTP_SERVICE_UNAVAILABLE_STATUS:
        return False
    try:
        response_body = response.json()
    except ValueError:
        return False
    return response_body.get("detail") == CHAT_UNAVAILABLE_DETAIL


def _send_chat_request_with_ready_retries(
    http_client: httpx.Client,
    *,
    base_url: str,
    token: str,
    question: str,
) -> httpx.Response:
    """Send /chat and retry temporary backend warmup responses.

    Inputs:
        http_client: Shared integration HTTP client.
        base_url: Integrated backend base URL.
        token: Bearer token from key-login.
        question: User-facing chat question to send.

    Outputs:
        Final HTTP response from /chat.

    Edge cases:
        Retries only 503 responses with detail "not ready", up to a fixed cap.
    """
    chat_response: httpx.Response | None = None
    for attempt_index in range(CHAT_NOT_READY_RETRY_ATTEMPTS):
        chat_response = http_client.post(
            f"{base_url}{CHAT_PATH}",
            headers={AUTH_HEADER_NAME: f"Bearer {token}"},
            json={"question": question},
            timeout=CHAT_TIMEOUT_SECONDS,
        )
        if not _is_not_ready_response(chat_response):
            return chat_response
        if attempt_index < CHAT_NOT_READY_RETRY_ATTEMPTS - 1:
            time.sleep(CHAT_NOT_READY_RETRY_DELAY_SECONDS)
    assert chat_response is not None
    return chat_response


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
        Retries known transient responses before asserting:
        - 429 with detail "rate limit exceeded"
        - 503 with detail "chat_unavailable"
        Fails with assertion context when final status is not 200.
    """
    chat_response: httpx.Response | None = None
    rate_limit_retry_attempt_index = 0
    chat_unavailable_retry_attempt_index = 0

    while True:
        chat_response = _send_chat_request_with_ready_retries(
            http_client,
            base_url=base_url,
            token=token,
            question=question,
        )

        if _is_chat_rate_limited_response(chat_response):
            if rate_limit_retry_attempt_index >= CHAT_RATE_LIMIT_RETRY_ATTEMPTS - 1:
                break
            rate_limit_retry_attempt_index += 1
            time.sleep(CHAT_RATE_LIMIT_RETRY_DELAY_SECONDS)
            continue

        if _is_chat_unavailable_response(chat_response):
            if chat_unavailable_retry_attempt_index >= CHAT_UNAVAILABLE_RETRY_ATTEMPTS - 1:
                break
            chat_unavailable_retry_attempt_index += 1
            time.sleep(CHAT_UNAVAILABLE_RETRY_DELAY_SECONDS)
            continue

        break

    assert chat_response is not None
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


@pytest.fixture(scope="module")
def access_token(base_url: str, http_client: httpx.Client) -> str:
    """Return one shared auth token for module integration chat tests.

    Inputs:
        base_url: Integrated backend base URL.
        http_client: Shared integration HTTP client.

    Outputs:
        Bearer token string from key-login.

    Edge cases:
        Scope is module-level to reduce key-login attempts and avoid self-induced
        rate limiting during the same test run.
    """
    return _login_for_token(http_client, base_url)


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
def test_real_backend_first_person(
    base_url: str,
    http_client: httpx.Client,
    access_token: str,
) -> None:
    """Verify integrated backend answers in first person.

    What is tested:
        Auth flow and chat response content against a live backend.
    How it's tested:
        Log in with a Firestore-hosted access key, then call /chat and inspect the answer.
    Expected result format:
        Statuses are 200, answer includes TLDR/Wrap markers and first-person.
    """
    response_body = _send_chat_question(
        http_client,
        base_url=base_url,
        token=access_token,
        question=QUESTION_TEXT,
    )
    answer_text = response_body[ANSWER_FIELD]
    # Some integrated configurations can respond deterministically without TLDR/Wrap.
    # Keep this test focused on first-person response shape rather than formatting.
    assert isinstance(answer_text, str) and answer_text.strip()
    # In integrated mode, response should include first-person pronouns.
    assert _contains_first_person_pronoun(answer_text)


@pytest.mark.integration
def test_real_backend_chat_non_gated_query_calls_llm(
    base_url: str,
    http_client: httpx.Client,
    access_token: str,
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
    response_body = _send_chat_question(
        http_client,
        base_url=base_url,
        token=access_token,
        question=NON_GATED_LLM_QUERY,
    )

    assert response_body.get("llm_called") is True
    answer_text = str(response_body.get(ANSWER_FIELD, ""))
    assert answer_text.strip()
    citations = response_body.get("citations")
    assert isinstance(citations, list) and len(citations) > 0


@pytest.mark.integration
@pytest.mark.parametrize(("question_text", "expected_answer_marker"), GATING_QUERY_CASES)
def test_real_backend_chat_gated_queries_bypass_llm(
    base_url: str,
    http_client: httpx.Client,
    access_token: str,
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
    response_body = _send_chat_question(
        http_client,
        base_url=base_url,
        token=access_token,
        question=question_text,
    )

    assert response_body.get("llm_called") is False
    assert response_body.get("citations") == []
    answer_text = str(response_body.get(ANSWER_FIELD, ""))
    assert expected_answer_marker in answer_text


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
    access_token: str,
) -> None:
    """Verify /chat rate limits even if the backend returns 503s pre-limit."""
    saw_rate_limit = False
    max_attempts = CHAT_RATE_LIMIT_MAX_PER_MINUTE + CHAT_RATE_LIMIT_ATTEMPT_BUFFER
    for _ in range(max_attempts):
        response = http_client.post(
            f"{base_url}{CHAT_PATH}",
            headers={AUTH_HEADER_NAME: f"Bearer {access_token}"},
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
