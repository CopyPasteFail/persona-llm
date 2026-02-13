"""Optional live integration test for Vertex AI Matching Engine.

The test is skipped by default because it requires cloud credentials and a
running deployment. When opted-in (``RUN_VERTEX_SEARCH_TEST=1`` with a sample
embedding provided), it exercises the production ``search_vector_store`` path
end-to-end against the configured Matching Engine endpoint to catch auth or API
regressions.
"""

from __future__ import annotations

import os

import pytest

from api import retrieval

RUN_VERTEX_SEARCH_ENV_VAR = "RUN_VERTEX_SEARCH_TEST"
VERTEX_TEST_EMBEDDING_ENV_VAR = "VERTEX_TEST_EMBEDDING"
VERTEX_TEST_TOP_K_ENV_VAR = "VERTEX_TEST_TOP_K"
DEFAULT_VERTEX_TEST_TOP_K = 4


@pytest.mark.integration
def test_vertex_vector_search_live_roundtrip():
    """Verify live Vertex search returns a list of results.

    What is tested:
        search_vector_store integration against the real Matching Engine endpoint.
    How it's tested:
        Parse env-provided embedding, call search_vector_store, and inspect output.
    Expected result format:
        The result is a list (contents vary with live service).
    """
    if os.getenv(RUN_VERTEX_SEARCH_ENV_VAR) != "1":
        pytest.skip(f"Set {RUN_VERTEX_SEARCH_ENV_VAR}=1 to enable live vector search test")

    embedding_env_value = os.getenv(VERTEX_TEST_EMBEDDING_ENV_VAR)
    if not embedding_env_value:
        pytest.skip(f"Provide {VERTEX_TEST_EMBEDDING_ENV_VAR} as comma-separated floats")

    embedding_values = _parse_embedding_values(embedding_env_value)
    if not embedding_values:
        pytest.skip(f"{VERTEX_TEST_EMBEDDING_ENV_VAR} did not yield any floats")

    top_k = int(os.getenv(VERTEX_TEST_TOP_K_ENV_VAR, str(DEFAULT_VERTEX_TEST_TOP_K)))

    if os.getenv("VECTOR_BACKEND") != "matching_engine":
        pytest.skip("Set VECTOR_BACKEND=matching_engine to enable Matching Engine test")
    for required_env in ("INDEX_ENDPOINT_ID", "DEPLOYED_INDEX_ID", "PROJECT_ID", "REGION"):
        if not os.getenv(required_env):
            pytest.skip(f"Missing {required_env}; configure Matching Engine env vars")
    retrieval.configure_vector_client(None)
    results = retrieval.search_vector_store(embedding_values, top_k=top_k)

    assert isinstance(results, list)


def _parse_embedding_values(embedding_env_value: str) -> list[float]:
    embedding_values: list[float] = []
    for raw_value in embedding_env_value.split(","):
        stripped_value = raw_value.strip()
        if not stripped_value:
            continue
        embedding_values.append(float(stripped_value))
    return embedding_values
