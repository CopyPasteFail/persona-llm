"""Integration tests for deployed CORS behavior.

Test cases covered:
- CORS allowlist: verifies the backend echoes Access-Control-Allow-Origin for
  the configured Hosting origin (derived from PROJECT_ID or provided explicitly
  via FRONTEND_ORIGIN) when calling /health with a matching Origin header.
- CORS denylist: verifies the backend does not echo Access-Control-Allow-Origin
  for an untrusted origin when calling /health with a fake Origin header.
"""

import os
from typing import Generator

import httpx
import pytest

BASE_URL_ENV = "NEXT_PUBLIC_API_URL"
BASE_URL_PREFIX = "http"
FRONTEND_ORIGIN_ENV = "FRONTEND_ORIGIN"
PROJECT_ID_ENV = "PROJECT_ID"
HEALTH_PATH = "/health"
HTTP_OK_STATUS = 200
ALLOW_ORIGIN_HEADER = "access-control-allow-origin"
NEGATIVE_ORIGIN = "https://not-allowed.example"
HEALTH_TIMEOUT_SECONDS = 10


def _get_base_url() -> str:
    base_url = os.getenv(BASE_URL_ENV, "")
    print(f"[cors] Target BASE_URL={base_url!r}")
    if not base_url or not base_url.startswith(BASE_URL_PREFIX):
        pytest.skip(f"Set {BASE_URL_ENV} to run CORS integration tests")
    return base_url.rstrip("/")


def _get_frontend_origin() -> str:
    frontend_origin = os.getenv(FRONTEND_ORIGIN_ENV)
    if frontend_origin:
        print(f"[cors] Using {FRONTEND_ORIGIN_ENV}={frontend_origin!r}")
        return frontend_origin
    project_id = os.getenv(PROJECT_ID_ENV)
    if not project_id:
        pytest.skip(
            f"Set {FRONTEND_ORIGIN_ENV} or {PROJECT_ID_ENV} to run CORS integration tests"
        )
    derived_origin = f"https://{project_id}.web.app"
    print(f"[cors] Derived frontend origin from {PROJECT_ID_ENV}: {derived_origin!r}")
    return derived_origin


@pytest.fixture(scope="module")
def base_url() -> str:
    return _get_base_url()


@pytest.fixture(scope="module")
def frontend_origin() -> str:
    return _get_frontend_origin()


@pytest.fixture(scope="module")
def http_client() -> Generator[httpx.Client, None, None]:
    client = httpx.Client()
    try:
        yield client
    finally:
        client.close()


@pytest.mark.integration
def test_cors_allows_frontend_origin(
    base_url: str,
    frontend_origin: str,
    http_client: httpx.Client,
) -> None:
    """Verify the backend allows the expected frontend origin.

    What is tested:
        CORS allow behavior for the configured Hosting origin.
    How it's tested:
        GET /health with Origin set to the expected frontend origin.
    Expected result format:
        Status is 200 and Access-Control-Allow-Origin matches the origin.
    """
    target_url = f"{base_url}{HEALTH_PATH}"
    print(f"[cors] allow check url={target_url!r} origin={frontend_origin!r}")
    response = http_client.get(
        target_url,
        headers={"Origin": frontend_origin},
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    assert response.status_code == HTTP_OK_STATUS
    assert response.headers.get(ALLOW_ORIGIN_HEADER) == frontend_origin


@pytest.mark.integration
def test_cors_rejects_unknown_origin(
    base_url: str,
    http_client: httpx.Client,
) -> None:
    """Verify the backend does not allow unknown origins.

    What is tested:
        CORS deny behavior for an untrusted origin.
    How it's tested:
        GET /health with Origin set to a fake origin.
    Expected result format:
        Status is 200 and Access-Control-Allow-Origin is not the fake origin.
    """
    target_url = f"{base_url}{HEALTH_PATH}"
    print(f"[cors] reject check url={target_url!r} origin={NEGATIVE_ORIGIN!r}")
    response = http_client.get(
        target_url,
        headers={"Origin": NEGATIVE_ORIGIN},
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    assert response.status_code == HTTP_OK_STATUS
    allow_origin = response.headers.get(ALLOW_ORIGIN_HEADER)
    assert allow_origin is None or allow_origin != NEGATIVE_ORIGIN
