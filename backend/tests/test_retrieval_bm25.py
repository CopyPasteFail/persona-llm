"""Unit tests for BM25 token filtering and field indexing behavior in `api.retrieval`.

The suite verifies:
- BM25 query token filtering removes stopwords/template words and short tokens.
- BM25 chunk indexing uses flat chunk fields and avoids toxic lexical leakage.
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
    """Verify BM25 chunk token extraction excludes configured toxic flat fields.

    Inputs:
    - Flat chunk with values in both retained and excluded fields.

    Outputs:
    - Assertions that retained fields contribute tokens while excluded fields do not.

    Edge cases:
    - Excluded fields can contain tempting high-idf terms and should still be ignored.
    """

    chunk: dict[str, Any] = {
        "chunk_id": "chunk-1",
        "text": "Platform delivery for Kubernetes clusters.",
        "section": "infra",
        "doc_id": "dentistry-product-001",
        "source_uri": "https://example.com/dentistry/product-001",
        "topics": ["kubernetes"],
        "tags": ["topic:terraform"],
        "extras": {"keywords": ["dentistry", "clinic"]},
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
            "chunk_id": "product-001",
            "profile": "product",
            "text": "I led product roadmap planning and launch coordination.",
            "section": "product",
            "doc_id": "do-you-have-experience-in-dentistry-product-001",
            "source_uri": "https://example.com/do-you-have-experience-in-dentistry",
            "topics": ["roadmap"],
            "tags": ["topic:product"],
            "extras": {"keywords": ["do", "you", "have", "experience", "dentistry"]},
        },
        "infra-001": {
            "chunk_id": "infra-001",
            "profile": "infra",
            "text": "I run Kubernetes clusters and Terraform pipelines.",
            "section": "infra",
            "topics": ["kubernetes", "terraform"],
            "tags": ["topic:kubernetes", "topic:terraform"],
        },
    }
    retrieval.configure_chunk_store(chunk_corpus)

    candidates = [
        {"chunk_id": "product-001", "distance": 0.20},
        {"chunk_id": "infra-001", "distance": 0.30},
    ]

    retrieval._CURRENT_QUERY.set("Do you have experience in dentistry?")  # pyright: ignore[reportPrivateUsage]
    dentistry_results = retrieval.apply_filters_and_boosting(candidates)
    dentistry_result_by_id = {item["chunk_id"]: item for item in dentistry_results}
    assert dentistry_result_by_id["product-001"]["bm25_score"] == 0.0

    retrieval._CURRENT_QUERY.set("Kubernetes Terraform platform delivery")  # pyright: ignore[reportPrivateUsage]
    infra_results = retrieval.apply_filters_and_boosting(candidates)
    infra_result_by_id = {item["chunk_id"]: item for item in infra_results}
    assert infra_result_by_id["infra-001"]["bm25_score"] > 0.0


def test_apply_filters_and_boosting_uses_profile_and_treats_unknown_as_neutral(
    reset_chunk_store: None,
) -> None:
    """Profile metadata should drive boosts and unknown profiles stay neutral."""

    del reset_chunk_store
    chunk_corpus: dict[str, dict[str, Any]] = {
        "infra-profile": {
            "chunk_id": "infra-profile",
            "profile": "infra",
            "text": "I built resilient platform automation.",
        },
        "product-profile": {
            "chunk_id": "product-profile",
            "profile": "product",
            "text": "I built resilient platform automation.",
        },
        "marketing-profile": {
            "chunk_id": "marketing-profile",
            "profile": "marketing",
            "text": "I built resilient platform automation.",
        },
    }
    retrieval.configure_chunk_store(chunk_corpus)

    candidates = [
        {"chunk_id": "infra-profile", "distance": 0.2},
        {"chunk_id": "product-profile", "distance": 0.2},
        {"chunk_id": "marketing-profile", "distance": 0.2},
    ]
    retrieval._CURRENT_QUERY.set("Tell me about your devops platform work")  # pyright: ignore[reportPrivateUsage]
    results = retrieval.apply_filters_and_boosting(candidates)
    result_by_id = {item["chunk_id"]: item for item in results}

    assert results[0]["chunk_id"] == "infra-profile"
    assert result_by_id["infra-profile"]["score"] > result_by_id["product-profile"]["score"]
    assert result_by_id["infra-profile"]["score"] > result_by_id["marketing-profile"]["score"]


def test_apply_filters_and_boosting_outputs_canonical_flat_ranked_records(
    reset_chunk_store: None,
) -> None:
    """Ranked retrieval output should never include legacy `id` or `metadata` keys.

    Inputs:
    - Flat chunk corpus configured in the in-memory runtime chunk store.
    - Candidate neighbor list with one matching chunk id.

    Outputs:
    - Assertions over each ranked record in retrieval output.

    Edge cases:
    - Verifies every ranked entry, not only top-1, to guard future list changes.
    """

    del reset_chunk_store
    chunk_corpus: dict[str, dict[str, Any]] = {
        "infra-guardrail": {
            "chunk_id": "infra-guardrail",
            "profile": "infra",
            "section": "Experience",
            "text": "I built Kubernetes platform automation.",
        }
    }
    retrieval.configure_chunk_store(chunk_corpus)
    retrieval._CURRENT_QUERY.set("kubernetes platform automation")  # pyright: ignore[reportPrivateUsage]

    ranked_results = retrieval.apply_filters_and_boosting(
        [{"chunk_id": "infra-guardrail", "distance": 0.1}]
    )

    assert ranked_results
    for ranked_record in ranked_results:
        assert "metadata" not in ranked_record
        assert "id" not in ranked_record


def test_configure_chunk_store_accepts_flat_chunk_records(
    reset_chunk_store: None,
) -> None:
    """Strict mode should accept flat schema-v3 chunk records."""

    del reset_chunk_store
    retrieval.configure_chunk_store(
        [
            {
                "chunk_id": "flat-1",
                "text": "flat chunk",
                "profile": "infra",
                "section": "Experience",
            }
        ]
    )

    snapshot = retrieval.get_chunk_store_snapshot()
    assert "flat-1" in snapshot


def test_normalize_chunk_record_rejects_nested_metadata_field() -> None:
    """Runtime chunk normalization should reject nested legacy metadata payloads.

    Inputs:
    - Chunk record with valid flat required fields plus a nested `metadata` object.

    Outputs:
    - Assertion that normalization raises RuntimeError.

    Edge cases:
    - Guards against reintroducing schema-v2 nested metadata at runtime.
    """

    legacy_chunk_record: dict[str, object] = {
        "chunk_id": "legacy-1",
        "text": "Legacy chunk",
        "metadata": {"section": "Experience"},
    }

    with pytest.raises(RuntimeError, match="without metadata"):
        retrieval._normalize_chunk_record(  # pyright: ignore[reportPrivateUsage]
            legacy_chunk_record
        )


def test_normalize_chunk_record_rejects_conflicting_legacy_id_alias() -> None:
    """Runtime chunk normalization should reject legacy `id` that conflicts with chunk_id.

    Inputs:
    - Chunk record where `chunk_id` and legacy `id` are both present and differ.

    Outputs:
    - Assertion that normalization raises RuntimeError with conflict context.

    Edge cases:
    - Error message should include both id values for quick diagnosis.
    """

    legacy_chunk_record: dict[str, object] = {
        "chunk_id": "legacy-1",
        "id": "different-1",
        "text": "Legacy chunk",
    }

    with pytest.raises(RuntimeError, match="legacy id.*different-1.*legacy-1"):
        retrieval._normalize_chunk_record(  # pyright: ignore[reportPrivateUsage]
            legacy_chunk_record
        )


def test_normalize_chunk_record_accepts_matching_legacy_id_alias() -> None:
    """Runtime chunk normalization should allow a legacy `id` alias that equals chunk_id.

    Inputs:
    - Chunk record with matching non-empty `chunk_id` and legacy `id` values.

    Outputs:
    - Normalized record preserving canonical fields and dropping legacy `id`.

    Edge cases:
    - Ensures tolerant alias handling does not leak legacy keys into runtime store.
    """

    legacy_chunk_record: dict[str, object] = {
        "chunk_id": "ok-1",
        "id": "ok-1",
        "text": "Canonical chunk",
    }

    normalized_record = retrieval._normalize_chunk_record(  # pyright: ignore[reportPrivateUsage]
        legacy_chunk_record
    )

    assert normalized_record is not None
    assert "id" not in normalized_record
    assert normalized_record["chunk_id"] == "ok-1"
