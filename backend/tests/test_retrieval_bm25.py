"""Unit tests for BM25 token filtering and field indexing behavior in `api.retrieval`.

The suite verifies:
- BM25 query token filtering removes stopwords/template words and short tokens.
- BM25 chunk indexing excludes toxic metadata fields (`doc_id`, `source_uri`, `extras`).
- Retrieval BM25 scoring avoids the dentistry false-positive on `product-001` while
  preserving positive BM25 signal for in-domain infra terms.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from api import retrieval


@pytest.fixture
def reset_chunk_store() -> Iterator[None]:
    """Ensure BM25 chunk/index globals are isolated per test.

    Inputs:
    - None.

    Outputs:
    - Yield control to the test with an empty configured chunk store.

    Edge cases:
    - Always resets state after the test, even when assertions fail.
    """

    retrieval.configure_chunk_store(None)
    yield
    retrieval.configure_chunk_store(None)


def test_tokenize_for_bm25_filters_stopwords_and_short_tokens() -> None:
    """Verify BM25 tokenization removes stopwords/template words and short terms.

    Inputs:
    - Query text containing required toxic terms, short tokens, and valid content terms.

    Outputs:
    - Assertions over the filtered BM25 token list.

    Edge cases:
    - Keeps 3+ character tokens (including numeric and alphanumeric terms like `k8s`).
    """

    query_text = "Do you have experience in dentistry with k8s and gcp in 2024?"
    raw_tokens = retrieval._tokenize(query_text)  # pyright: ignore[reportPrivateUsage]
    bm25_tokens = retrieval._tokenize_for_bm25(query_text)  # pyright: ignore[reportPrivateUsage]

    assert "do" in raw_tokens
    assert "you" in raw_tokens
    assert "have" in raw_tokens
    assert "in" in raw_tokens
    assert "experience" in raw_tokens

    assert "do" not in bm25_tokens
    assert "you" not in bm25_tokens
    assert "have" not in bm25_tokens
    assert "in" not in bm25_tokens
    assert "with" not in bm25_tokens
    assert "experience" not in bm25_tokens

    assert "dentistry" in bm25_tokens
    assert "k8s" in bm25_tokens
    assert "gcp" in bm25_tokens
    assert "2024" in bm25_tokens
    assert all(len(token) >= 3 for token in bm25_tokens)


def test_extract_chunk_tokens_excludes_doc_id_source_uri_and_extras() -> None:
    """Verify BM25 chunk token extraction excludes configured toxic metadata fields.

    Inputs:
    - Chunk with metadata values in both retained and excluded fields.

    Outputs:
    - Assertions that retained fields contribute tokens while excluded fields do not.

    Edge cases:
    - Excluded fields can contain tempting high-idf terms and should still be ignored.
    """

    chunk: dict[str, Any] = {
        "id": "chunk-1",
        "text": "Platform delivery for Kubernetes clusters.",
        "metadata": {
            "section": "infra",
            "doc_id": "dentistry-product-001",
            "source_uri": "https://example.com/dentistry/product-001",
            "topics": ["kubernetes"],
            "tags": ["topic:terraform"],
            "extras": {"keywords": ["dentistry", "clinic"]},
        },
    }

    extracted_tokens = retrieval._extract_chunk_tokens(chunk)  # pyright: ignore[reportPrivateUsage]

    assert "platform" in extracted_tokens
    assert "delivery" in extracted_tokens
    assert "kubernetes" in extracted_tokens
    assert "infra" in extracted_tokens
    assert "terraform" in extracted_tokens

    assert "dentistry" not in extracted_tokens
    assert "clinic" not in extracted_tokens
    assert "https" not in extracted_tokens
    assert "example" not in extracted_tokens


def test_apply_filters_and_boosting_avoids_dentistry_false_positive(
    reset_chunk_store: None,
) -> None:
    """Verify BM25 score is zero for `product-001` dentistry query after filtering/index changes.

    Inputs:
    - Chunk corpus where `product-001` only carries dentistry terms in excluded fields.
    - Dentistry-like query that previously over-matched boilerplate tokens.

    Outputs:
    - Assertions over BM25 raw scores in retrieval output.

    Edge cases:
    - Ensures infra query still returns positive BM25 for true lexical matches.
    """

    del reset_chunk_store
    chunk_corpus: dict[str, dict[str, Any]] = {
        "product-001": {
            "id": "product-001",
            "text": "I led product roadmap planning and launch coordination.",
            "metadata": {
                "section": "product",
                "doc_id": "do-you-have-experience-in-dentistry-product-001",
                "source_uri": "https://example.com/do-you-have-experience-in-dentistry",
                "topics": ["roadmap"],
                "tags": ["topic:product"],
                "extras": {"keywords": ["do", "you", "have", "experience", "dentistry"]},
            },
        },
        "infra-001": {
            "id": "infra-001",
            "text": "I run Kubernetes clusters and Terraform pipelines.",
            "metadata": {
                "section": "infra",
                "topics": ["kubernetes", "terraform"],
                "tags": ["topic:kubernetes", "topic:terraform"],
            },
        },
    }
    retrieval.configure_chunk_store(chunk_corpus)

    candidates = [
        {"id": "product-001", "distance": 0.20},
        {"id": "infra-001", "distance": 0.30},
    ]

    retrieval._CURRENT_QUERY.set("Do you have experience in dentistry?")  # pyright: ignore[reportPrivateUsage]
    dentistry_results = retrieval.apply_filters_and_boosting(candidates)
    dentistry_result_by_id = {item["id"]: item for item in dentistry_results}
    assert dentistry_result_by_id["product-001"]["bm25_score"] == 0.0

    retrieval._CURRENT_QUERY.set("Kubernetes Terraform platform delivery")  # pyright: ignore[reportPrivateUsage]
    infra_results = retrieval.apply_filters_and_boosting(candidates)
    infra_result_by_id = {item["id"]: item for item in infra_results}
    assert infra_result_by_id["infra-001"]["bm25_score"] > 0.0


def test_apply_filters_and_boosting_uses_profile_and_treats_unknown_as_neutral(
    reset_chunk_store: None,
) -> None:
    """Profile metadata should drive boosts and unknown profiles stay neutral."""

    del reset_chunk_store
    chunk_corpus: dict[str, dict[str, Any]] = {
        "infra-profile": {
            "id": "infra-profile",
            "text": "I built resilient platform automation.",
            "metadata": {"profile": "infra"},
        },
        "product-profile": {
            "id": "product-profile",
            "text": "I built resilient platform automation.",
            "metadata": {"profile": "product"},
        },
        "marketing-profile": {
            "id": "marketing-profile",
            "text": "I built resilient platform automation.",
            "metadata": {"profile": "marketing"},
        },
    }
    retrieval.configure_chunk_store(chunk_corpus)

    candidates = [
        {"id": "infra-profile", "distance": 0.2},
        {"id": "product-profile", "distance": 0.2},
        {"id": "marketing-profile", "distance": 0.2},
    ]
    retrieval._CURRENT_QUERY.set("Tell me about your devops platform work")  # pyright: ignore[reportPrivateUsage]
    results = retrieval.apply_filters_and_boosting(candidates)
    result_by_id = {item["id"]: item for item in results}

    assert results[0]["id"] == "infra-profile"
    assert result_by_id["infra-profile"]["score"] > result_by_id["product-profile"]["score"]
    assert result_by_id["infra-profile"]["score"] > result_by_id["marketing-profile"]["score"]
