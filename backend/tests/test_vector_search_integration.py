"""Optional live integration test for Vertex AI Matching Engine.

The test is skipped by default because it requires real cloud credentials and a
running deployment. When opted-in (``RUN_VERTEX_SEARCH_TEST=1`` with a sample
embedding provided), it exercises the production ``search_vector_store`` path
end-to-end against the configured Matching Engine endpoint to catch auth or API
regressions.
"""

from __future__ import annotations

import os

import pytest

from api import retrieval


@pytest.mark.integration
def test_vertex_vector_search_live_roundtrip():
    """Optional live test; requires credentials and a real Matching Engine deployment."""
    if os.getenv("RUN_VERTEX_SEARCH_TEST") != "1":
        pytest.skip("Set RUN_VERTEX_SEARCH_TEST=1 to enable live vector search test")

    embedding_env = os.getenv("VERTEX_TEST_EMBEDDING")
    if not embedding_env:
        pytest.skip("Provide VERTEX_TEST_EMBEDDING as comma-separated floats")

    embedding = [float(piece.strip()) for piece in embedding_env.split(",") if piece.strip()]
    if not embedding:
        pytest.skip("VERTEX_TEST_EMBEDDING did not yield any floats")

    top_k = int(os.getenv("VERTEX_TEST_TOP_K", "4"))

    retrieval.configure_vector_client(None)
    results = retrieval.search_vector_store(embedding, top_k=top_k)

    assert isinstance(results, list)
