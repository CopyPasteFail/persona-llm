"""Unit tests for the vector search adapter in ``api.retrieval``.

The suite verifies that ``search_vector_store`` correctly normalizes query
embeddings, delegates to an injected client, and gracefully handles guard-rail
conditions like empty vectors or zero ``top_k``. It also confirms that calling
``configure_vector_client`` swaps active stubs, which keeps downstream code
testable without real Vertex AI dependencies.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterator, List, Sequence, Tuple

import pytest

from api import retrieval


class RecordingVectorClient:
    def __init__(self) -> None:
        """Create a recording client with a preset response to verify calls and outputs."""
        self.calls: List[Tuple[List[float], int]] = []
        self.return_value: List[Dict[str, Any]] = [{"id": "n-1", "distance": 0.12}]

    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        """Record the query inputs and return the preset results for test assertions."""
        self.calls.append((list(embedding), top_k))
        return self.return_value


@pytest.fixture
def stub_client() -> Iterator[RecordingVectorClient]:
    """Provide a configured client stub and reset the vector client after the test."""
    client = RecordingVectorClient()
    retrieval.configure_vector_client(client)
    yield client
    retrieval.configure_vector_client(None)


def test_search_vector_store_normalizes_and_invokes_client(
    stub_client: RecordingVectorClient,
) -> None:
    """Verify search normalizes embeddings and delegates to the client.

    What is tested:
        search_vector_store normalization and client invocation behavior.
    How it's tested:
        Call search_vector_store with a non-normalized vector and inspect calls.
    Expected result format:
        Result equals stub return value and recorded embedding is unit-normalized.
    """
    result = retrieval.search_vector_store([3.0, 4.0], top_k=5)

    assert result == stub_client.return_value
    assert len(stub_client.calls) == 1
    embedding, top_k = stub_client.calls[0]
    assert top_k == 5
    assert math.isclose(sum(x * x for x in embedding), 1.0)
    assert embedding[0] < embedding[1]


def test_search_vector_store_returns_empty_when_no_vector(
    stub_client: RecordingVectorClient,
) -> None:
    """Verify empty input or top_k=0 short-circuits.

    What is tested:
        Guard-rail behavior for missing embeddings or zero top_k.
    How it's tested:
        Call search_vector_store with empty/None vectors and top_k=0.
    Expected result format:
        Each call returns [] and the client receives no queries.
    """
    assert retrieval.search_vector_store([], top_k=3) == []
    assert retrieval.search_vector_store(None, top_k=3) == []
    assert retrieval.search_vector_store([1.0], top_k=0) == []
    assert stub_client.calls == []


def test_configure_vector_client_swaps_out_previous_stub() -> None:
    """Verify configure_vector_client swaps the active stub.

    What is tested:
        Client replacement behavior for subsequent search calls.
    How it's tested:
        Configure one stub, query, swap to another stub, and query again.
    Expected result format:
        Each query returns the corresponding stub's return value and records calls.
    """
    first = RecordingVectorClient()
    second = RecordingVectorClient()

    retrieval.configure_vector_client(first)
    assert retrieval.search_vector_store([1.0, 0.0], top_k=1) == first.return_value
    assert first.calls

    retrieval.configure_vector_client(second)
    assert retrieval.search_vector_store([0.0, 1.0], top_k=2) == second.return_value
    assert second.calls

    retrieval.configure_vector_client(None)
