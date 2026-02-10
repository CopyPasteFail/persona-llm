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
        self.calls: List[Tuple[List[float], int]] = []
        self.return_value: List[Dict[str, Any]] = [{"id": "n-1", "distance": 0.12}]

    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        self.calls.append((list(embedding), top_k))
        return self.return_value


@pytest.fixture
def stub_client() -> Iterator[RecordingVectorClient]:
    client = RecordingVectorClient()
    retrieval.configure_vector_client(client)
    yield client
    retrieval.configure_vector_client(None)


def test_search_vector_store_normalizes_and_invokes_client(
    stub_client: RecordingVectorClient,
) -> None:
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
    assert retrieval.search_vector_store([], top_k=3) == []
    assert retrieval.search_vector_store(None, top_k=3) == []
    assert retrieval.search_vector_store([1.0], top_k=0) == []
    assert stub_client.calls == []


def test_configure_vector_client_swaps_out_previous_stub() -> None:
    first = RecordingVectorClient()
    second = RecordingVectorClient()

    retrieval.configure_vector_client(first)
    assert retrieval.search_vector_store([1.0, 0.0], top_k=1) == first.return_value
    assert first.calls

    retrieval.configure_vector_client(second)
    assert retrieval.search_vector_store([0.0, 1.0], top_k=2) == second.return_value
    assert second.calls

    retrieval.configure_vector_client(None)
