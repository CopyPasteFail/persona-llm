"""
Retrieval helpers. Keep pure and side-effect free.

Name normalization uses the configured persona name from settings.PERSONA_NAME.
It generates regexes for *all permutations* of up to the configured name parts.
For example: "Alex Taylor" -> ["Alex", "Taylor", "Alex Taylor", "Taylor Alex"]

To avoid the issue where regexes were compiled before the environment was
loaded (tests failed because PERSONA_NAME wasn't set yet), regexes are now
compiled lazily each time normalize_question_for_first_person is called.
"""
from __future__ import annotations

import gzip
import io
import itertools
import json
import logging
import math
import os
import re
import threading
from collections import defaultdict
from contextvars import ContextVar
from pathlib import Path
from types import MappingProxyType
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    runtime_checkable,
    cast,
)

from . import dataset_cache, vector_backends
from .prompts import QUESTION_PREFIX
from .settings import settings

# Apostrophe: support curly and straight
_APOS = r"[’']"

# Business-tuned thresholds and weights.
_DEFAULT_TOP_K = 8
_MAX_CONTEXT_CHUNKS = 8
_PROFILE_BOOST = 0.05
_TOPIC_BOOST = 0.02
_MAX_TOPIC_BOOST = 0.06
_DEBUG_NEIGHBOR_SAMPLE = 5
_DEBUG_BM25_SAMPLE = 5

# BM25 parameters.
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_MIN_TOKEN_LENGTH = 3
_BM25_STOPWORDS: Set[str] = {
    "a",
    "about",
    "after",
    "again",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "before",
    "being",
    "between",
    "both",
    "btw",
    "built",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "done",
    "during",
    "each",
    "experience",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "let",
    "like",
    "may",
    "me",
    "more",
    "most",
    "much",
    "my",
    "nor",
    "not",
    "now",
    "of",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "out",
    "over",
    "please",
    "same",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "tell",
    "under",
    "until",
    "up",
    "us",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "worked",
    "would",
    "you",
    "your",
    "yours",
    "i",
}

logger = logging.getLogger(" api.retrieval")
if os.getenv("RETRIEVAL_DEBUG") == "1":
    logger.setLevel(logging.DEBUG)


def _persona_variants() -> list[str]:
    """
    Generate all permutations of all non-empty subsets of the persona name parts.
    Example: "Alex Taylor" -> ["Alex", "Taylor", "Alex Taylor", "Taylor Alex"].
    Enforced max words in settings prevents combinatorial explosion.

    Inputs:
        None. Uses settings.PERSONA_NAME.
    Outputs:
        A list of unique name variants in deterministic order.
    Edge cases:
        Returns an empty list if no persona name is configured.
    """
    full_name = (settings.PERSONA_NAME or "").strip()
    if not full_name:
        return []

    name_parts = full_name.split()
    variants: list[str] = []
    part_count = len(name_parts)
    for subset_size in range(1, part_count + 1):  # subset size
        for permutation in itertools.permutations(name_parts, subset_size):
            variants.append(" ".join(permutation))

    # de-dupe while preserving order
    seen_variants: set[str] = set()
    unique_variants: list[str] = []
    for variant in variants:
        if variant not in seen_variants:
            seen_variants.add(variant)
            unique_variants.append(variant)
    return unique_variants


def _compile_regexes():
    """
    Build regexes on demand using the current PERSONA_NAME from settings.
    Returns (possessive_regex, bare_regex).

    Inputs:
        None. Uses settings.PERSONA_NAME.
    Outputs:
        A tuple of compiled regexes or (None, None) if no persona name exists.
    Edge cases:
        Returns (None, None) when no variants are produced.
    """
    persona_variants = _persona_variants()
    if not persona_variants:
        return None, None

    # IMPORTANT: longest-first so "Alex Taylor" beats "Alex" then "Taylor"
    persona_variants = sorted(persona_variants, key=len, reverse=True)

    alternatives = "|".join(re.escape(variant) for variant in persona_variants)
    group = fr"(?:{alternatives})"

    possessive = re.compile(
        rf"(?<![\w.+-/]){group}{_APOS}s\b(?!@)",
        re.IGNORECASE | re.UNICODE,
    )

    bare = re.compile(
        rf"(?<![@/\w]){group}\b(?!{_APOS}s\w)(?!@)",
        re.IGNORECASE | re.UNICODE,
    )

    return possessive, bare


def normalize_question_for_first_person(question: str) -> str:
    """
    Convert references like "<Name>'s" -> "your" and "<Name>" -> "I",
    while avoiding emails, usernames and inside-word matches.

    Regexes are compiled lazily from settings.PERSONA_NAME so tests
    can inject env vars without being broken by early imports.

    Inputs:
        question: The raw user question to normalize.
    Outputs:
        A normalized string with persona references rewritten.
    Edge cases:
        Returns the input unchanged if it is empty or no persona name is configured.
    """
    if not question:
        return question

    possessive, bare = _compile_regexes()
    if not possessive or not bare:
        return question

    normalized = possessive.sub("your", question)
    normalized = bare.sub("I", normalized)
    return normalized


# ---- Real integrations (unimplemented in mock) ----

_CURRENT_QUERY: ContextVar[str] = ContextVar("_current_query", default="")


@runtime_checkable
class _EmbeddingClient(Protocol):
    def embed(self, text: str) -> Optional[Sequence[float]]:
        ...


_embedding_client: Optional[_EmbeddingClient] = None


def configure_embedding_client(client: Optional[_EmbeddingClient]) -> None:
    """
    Override the embedding client (facilitates tests).

    Inputs:
        client: The embedding client to use, or None to clear.
    Outputs:
        None. Updates the module-level client reference.
    Edge cases:
        Passing None disables embedding until reconfigured.
    """
    global _embedding_client
    _embedding_client = client


def configure_vertex_embedding_client(*, project: str, region: str, model_name: str) -> None:
    """
    Configure an embedding client explicitly (used by app startup).

    Inputs:
        project: GCP project id.
        region: GCP region for Vertex.
        model_name: Embedding model name (e.g. text-embedding-004, gemini-embedding-001).
    Outputs:
        None. Installs a configured embedding client.
    Edge cases:
        Raises if configuration is invalid when used for the first embed call.
    """
    configure_embedding_client(
        _GenaiEmbeddingClient(project=project, region=region, model_name=model_name)
    )


def _get_embedding_client() -> _EmbeddingClient:
    """
    Return the configured embedding client.

    Inputs:
        None.
    Outputs:
        The configured embedding client.
    Edge cases:
        Raises RuntimeError if the client has not been configured.
    """
    global _embedding_client
    if _embedding_client is None:
        raise RuntimeError(
            "Embedding client is not configured. Call configure_embedding_client() "
            "or configure_vertex_embedding_client() during app startup."
        )
    return _embedding_client


def embed_query(question: str) -> Optional[List[float]]:
    """
    Embed the normalized question using Vertex AI Text Embedding model.

    The question text is stored in a ContextVar so downstream ranking logic can
    access it without threading issues (FastAPI async requests).

    Inputs:
        question: The raw user question string.
    Outputs:
        A list of floats or None when no embedding is produced.
    Edge cases:
        Returns None for empty questions or when the embedding client returns no values.
    Concurrency:
        Uses ContextVar to isolate per-request query state across async contexts.
    """
    normalized_question = (question or "").strip()
    _CURRENT_QUERY.set(normalized_question)
    if not normalized_question:
        return None

    client = _get_embedding_client()
    try:
        raw_vector = client.embed(normalized_question)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to embed query (length={len(normalized_question)})"
        ) from exc

    if not raw_vector:
        return None

    return [float(value) for value in raw_vector]


@runtime_checkable
class _VectorSearchClient(Protocol):
    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        ...


_vector_client: Optional[_VectorSearchClient] = None


def configure_vector_client(client: Optional[_VectorSearchClient]) -> None:
    """
    Override the underlying vector search client (primarily used in tests).

    Inputs:
        client: The vector search client to use, or None to clear.
    Outputs:
        None. Updates the module-level client reference.
    Edge cases:
        Passing None forces a fresh backend fetch on the next query.
    """
    global _vector_client
    _vector_client = client


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    """
    Return a unit-normalized copy of the provided vector.

    Inputs:
        vector: Sequence of floats to normalize.
    Outputs:
        A new list of floats, scaled to unit length when norm > 0.
    Edge cases:
        Returns a float-cast copy of the input when norm is zero.
    """
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm == 0:
        return [float(value) for value in vector]
    scale = 1.0 / norm
    return [float(value) * scale for value in vector]


def _get_vector_client() -> _VectorSearchClient:
    """
    Return the configured vector search client, creating it if missing.

    Inputs:
        None.
    Outputs:
        A vector search client instance.
    Edge cases:
        Lazily loads the backend the first time this is called.
    """
    global _vector_client
    if _vector_client is None:
        _vector_client = vector_backends.get_vector_backend()
    return _vector_client


def search_vector_store(
    embedding: Optional[Sequence[float]],
    top_k: int = _DEFAULT_TOP_K,
) -> List[Dict[str, Any]]:
    """
    Normalize and query the vector backend, returning candidate neighbors.

    Inputs:
        embedding: The raw embedding vector to search with.
        top_k: Number of neighbors to request from the backend.
    Outputs:
        A list of neighbor records from the backend.
    Edge cases:
        Returns an empty list when no embedding is provided or top_k <= 0.
    """
    if embedding is None:
        return []
    if top_k <= 0:
        return []

    vector = list(embedding)
    if not vector:
        return []

    normalized = _l2_normalize(vector)
    client = _get_vector_client()
    neighbors = client.query(normalized, top_k=top_k)
    logger.debug(
        " vector_search: top_k=%d neighbors=%d distances=%s",
        top_k,
        len(neighbors),
        [
            float(neighbor.get("distance", 0.0))
            for neighbor in neighbors[:_DEBUG_NEIGHBOR_SAMPLE]
        ],
    )
    return neighbors


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_INFRA_HINTS: Set[str] = {
    "sre",
    "devops",
    "infra",
    "platform",
    "kubernetes",
    "k8s",
    "helm",
    "terraform",
    "cicd",
    "ci",
    "cd",
    "observability",
    "incident",
    "oncall",
    "reliability",
    "ops",
    "automation",
    "prometheus",
    "grafana",
    "ansible",
    "cloud",
    "gcp",
    "aws",
}
_PRODUCT_HINTS: Set[str] = {
    "product",
    "roadmap",
    "launch",
    "stakeholder",
    "user",
    "research",
    "vision",
    "strategy",
    "okr",
    "kpi",
    "metrics",
    "prioritize",
    "gtm",
    "go",
    "market",
    "requirements",
    "backlog",
    "discovery",
    "ux",
    "customer",
}


def _tokenize(text: str) -> List[str]:
    """
    Tokenize text into lowercase alphanumeric terms.

    Inputs:
        text: Raw input string.
    Outputs:
        A list of lowercase tokens.
    Edge cases:
        Returns an empty list for empty inputs.
    """
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _filter_tokens_for_bm25(raw_tokens: Iterable[str]) -> List[str]:
    """
    Filter pre-tokenized terms for BM25 indexing and query scoring.

    Inputs:
        raw_tokens: Lowercased alphanumeric tokens from `_tokenize`.
    Outputs:
        Filtered token list preserving order and duplicates.
    Edge cases:
        Removes terms shorter than three characters and stopword/template terms.
    """
    filtered_tokens: List[str] = []
    for raw_token in raw_tokens:
        if len(raw_token) < _BM25_MIN_TOKEN_LENGTH:
            continue
        if raw_token in _BM25_STOPWORDS:
            continue
        filtered_tokens.append(raw_token)
    return filtered_tokens


def _tokenize_for_bm25(text: str) -> List[str]:
    """
    Tokenize text for BM25 use only, applying retrieval-specific filtering.

    Inputs:
        text: Raw input string.
    Outputs:
        Filtered BM25 token list preserving token multiplicity.
    Edge cases:
        Empty inputs produce an empty list.
    """
    return _filter_tokens_for_bm25(_tokenize(text))


def _distance_to_similarity(distance: float) -> float:
    """
    Convert a vector distance into a bounded similarity score.

    Inputs:
        distance: A raw distance value from the vector index.
    Outputs:
        A similarity score in (0, 1].
    Edge cases:
        Returns 0.0 for NaN distances and uses absolute value for negatives.
    """
    if math.isnan(distance):
        return 0.0
    if distance < 0:
        distance = abs(distance)
    return 1.0 / (1.0 + distance)


def _normalize_bm25(score: float) -> float:
    """
    Normalize a raw BM25 score into a (0, 1) range.

    Inputs:
        score: Raw BM25 score.
    Outputs:
        Normalized BM25 value.
    Edge cases:
        Returns 0.0 for non-positive scores.
    """
    if score <= 0:
        return 0.0
    return score / (score + 1.0)


def _chunk_profile(chunk: Mapping[str, Any]) -> Optional[str]:
    """
    Extract the profile label from a flat chunk record.

    Inputs:
        chunk: Chunk mapping.
    Outputs:
        A lowercase profile string, or None if not present.
    Edge cases:
        Returns None when `profile` is missing or blank.
    """
    profile = chunk.get("profile")
    if isinstance(profile, str) and profile:
        return profile.lower()
    return None


def _chunk_topics(chunk: Mapping[str, Any]) -> Set[str]:
    """
    Extract topic labels from a flat chunk record.

    Inputs:
        chunk: Chunk mapping.
    Outputs:
        A set of lowercase topic strings.
    Edge cases:
        Supports both "topics" field and "topic:" tag prefixes.
    """
    topics: Set[str] = set()
    raw_topics = chunk.get("topics")
    if isinstance(raw_topics, (list, tuple, set)):
        for topic in cast(Iterable[Any], raw_topics):
            if isinstance(topic, str):
                topics.add(topic.lower())
    tags = chunk.get("tags")
    if isinstance(tags, (list, tuple, set)):
        for tag in cast(Iterable[Any], tags):
            if isinstance(tag, str) and tag.startswith("topic:"):
                topics.add(tag.split(":", 1)[1].lower())

    return topics


def _classify_query_profile(question: str) -> Optional[str]:
    """
    Classify a query as infra or product profile based on keyword hints.

    Inputs:
        question: Raw user question.
    Outputs:
        "infra", "product", or None if ambiguous.
    Edge cases:
        Returns None when tokens overlap or are empty.
        Non-{"infra","product"} chunk profiles are treated as neutral.
    """
    tokens = set(_tokenize(question))
    if not tokens:
        return None

    infra_hits = tokens & _INFRA_HINTS
    product_hits = tokens & _PRODUCT_HINTS
    if infra_hits and not product_hits:
        return "infra"
    if product_hits and not infra_hits:
        return "product"
    return None


def _extract_chunk_tokens(chunk: Mapping[str, Any]) -> List[str]:
    """
    Extract searchable tokens from flat chunk text and selected fields.

    Inputs:
        chunk: Chunk record containing text and selected flat fields.
    Outputs:
        A list of BM25-filtered tokens from text, section, topics, and tags.
    Edge cases:
        Skips non-string fields and missing optional entries.
    """
    tokens: List[str] = []
    text = chunk.get("text")
    if isinstance(text, str):
        tokens.extend(_tokenize_for_bm25(text))

    section_value = chunk.get("section")
    if isinstance(section_value, str):
        tokens.extend(_tokenize_for_bm25(section_value))

    topics_value = chunk.get("topics")
    if isinstance(topics_value, (list, tuple, set)):
        for topic in cast(Iterable[Any], topics_value):
            if isinstance(topic, str):
                tokens.extend(_tokenize_for_bm25(topic))

    tags_value = chunk.get("tags")
    if isinstance(tags_value, (list, tuple, set)):
        for tag in cast(Iterable[Any], tags_value):
            if isinstance(tag, str):
                tokens.extend(_tokenize_for_bm25(tag))
    return tokens


def _normalize_chunk_record(
    raw_chunk: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Normalize a chunk record to the canonical flat runtime shape.

    Inputs:
        raw_chunk: Raw chunk record from cache/test/side-store.
    Outputs:
        Flat chunk record with `chunk_id` and `text`.
    Edge cases:
        Raises RuntimeError when legacy `metadata` is present.
        Allows legacy `id` only when it is a non-empty alias of `chunk_id`.
        Raises RuntimeError when legacy `id` conflicts with `chunk_id`.
        Returns None when required fields are missing.
    """
    chunk_id_value = raw_chunk.get("chunk_id")
    if "metadata" in raw_chunk:
        raise RuntimeError("Chunk records must be flat schema-v3 objects without metadata")
    if "id" in raw_chunk:
        legacy_id_value = raw_chunk.get("id")
        if (
            not isinstance(legacy_id_value, str)
            or not legacy_id_value
            or not isinstance(chunk_id_value, str)
            or not chunk_id_value
            or legacy_id_value != chunk_id_value
        ):
            raise RuntimeError(
                "Chunk record legacy id conflicts with chunk_id "
                f"(id={legacy_id_value!r}, chunk_id={chunk_id_value!r})"
            )

    text_value = raw_chunk.get("text")
    if not isinstance(text_value, str):
        return None

    if not isinstance(chunk_id_value, str) or not chunk_id_value:
        return None

    normalized_record: Dict[str, Any] = dict(raw_chunk)
    normalized_record["chunk_id"] = chunk_id_value
    normalized_record["text"] = text_value
    normalized_record.pop("id", None)
    return normalized_record


def _chunk_metadata_for_display(chunk: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Build a lightweight metadata map for display from flat chunk fields.

    Inputs:
        chunk: Flat chunk record.
    Outputs:
        Metadata mapping with only display-relevant fields.
    Edge cases:
        Missing optional fields are omitted.
    """
    metadata: Dict[str, Any] = {}
    for field_name in (
        "doc_id",
        "chunk_id",
        "position",
        "profile",
        "section",
        "start_year",
        "end_year",
        "topics",
        "tags",
        "lang",
        "updated_at",
        "source_uri",
        "permissions",
        "extras",
    ):
        field_value = chunk.get(field_name)
        if field_value is None:
            continue
        if field_name in {"topics", "tags", "permissions"} and isinstance(
            field_value, (list, tuple, set)
        ):
            metadata[field_name] = list(cast(Iterable[Any], field_value))
            continue
        if field_name == "extras" and isinstance(field_value, Mapping):
            metadata[field_name] = dict(cast(Mapping[str, Any], field_value))
            continue
        metadata[field_name] = field_value
    return metadata


class _Bm25Index:
    """
    Lightweight BM25 index for in-memory chunk search.

    Inputs:
        chunks: Mapping of chunk id to chunk record.
    Outputs:
        A BM25 index instance able to score queries.
    Edge cases:
        Handles empty corpora by producing zero scores.
    """

    def __init__(self, chunks: Mapping[str, Mapping[str, Any]]) -> None:
        self._doc_count = len(chunks)
        self._lengths: Dict[str, int] = {}
        self._avg_len: float = 0.0
        self._postings: Dict[str, Dict[str, int]] = defaultdict(dict)

        total_len = 0
        for chunk_id, chunk in chunks.items():
            tokens = _extract_chunk_tokens(chunk)
            length = len(tokens)
            self._lengths[chunk_id] = length
            total_len += length
            if not tokens:
                continue
            freqs: Dict[str, int] = defaultdict(int)
            for token in tokens:
                freqs[token] += 1
            for token, freq in freqs.items():
                self._postings[token][chunk_id] = freq

        self._avg_len = (total_len / self._doc_count) if self._doc_count else 0.0
        self._k1 = _BM25_K1
        self._b = _BM25_B

    def score(self, query_tokens: Iterable[str]) -> Dict[str, float]:
        """
        Score each chunk for the provided query tokens.

        Inputs:
            query_tokens: Iterable of pre-tokenized query terms.
        Outputs:
            Mapping of chunk id to BM25 score.
        Edge cases:
            Returns an empty dict when no query tokens match the corpus.
        """
        scores: Dict[str, float] = defaultdict(float)
        query_terms = [token for token in query_tokens if token in self._postings]
        if not query_terms:
            return {}

        for token in query_terms:
            postings = self._postings[token]
            df = len(postings)
            if df == 0:
                continue
            idf = math.log(1 + (self._doc_count - df + 0.5) / (df + 0.5))
            for chunk_id, tf in postings.items():
                doc_len = self._lengths.get(chunk_id, 0)
                denom = tf + self._k1 * (
                    1 - self._b + self._b * (doc_len / (self._avg_len or 1.0))
                )
                scores[chunk_id] += idf * (tf * (self._k1 + 1)) / denom
        return dict(scores)


_chunk_lock = threading.Lock()
_chunks_by_id: Optional[Dict[str, Dict[str, Any]]] = None
_bm25_index: Optional[_Bm25Index] = None


def configure_chunk_store(
    chunks: Optional[Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]]]
) -> None:
    """
    Allow tests or callers to inject a deterministic chunk corpus.

    Inputs:
        chunks: Iterable or mapping of chunk records; None clears the store.
    Outputs:
        None. Updates module-level chunk storage and BM25 index.
    Edge cases:
        Skips entries missing ids or text fields.
    Concurrency:
        Protected by _chunk_lock for atomic swap of shared state.
    """
    global _chunks_by_id, _bm25_index
    with _chunk_lock:
        if chunks is None:
            _chunks_by_id = None
            _bm25_index = None
            return

        mapping: Dict[str, Dict[str, Any]] = {}
        if isinstance(chunks, Mapping):
            typed_chunks = cast(Mapping[str, Mapping[str, Any]], chunks)
            for chunk_id, chunk in typed_chunks.items():
                if not isinstance(chunk_id, str):
                    raise RuntimeError("Chunk store mapping key must be a string chunk_id.")
                normalized_record = _normalize_chunk_record(chunk)
                if normalized_record is None:
                    missing_fields: List[str] = []
                    chunk_id_value = chunk.get("chunk_id")
                    text_value = chunk.get("text")
                    if not isinstance(chunk_id_value, str) or not chunk_id_value:
                        missing_fields.append("chunk_id")
                    if not isinstance(text_value, str):
                        missing_fields.append("text")
                    missing_fields_value = ", ".join(missing_fields) if missing_fields else "chunk_id, text"
                    raise RuntimeError(
                        "Invalid chunk record for mapping key "
                        f"{chunk_id!r}: missing/invalid {missing_fields_value}."
                    )
                normalized_chunk_id = str(normalized_record.get("chunk_id"))
                if chunk_id != normalized_chunk_id:
                    raise RuntimeError(
                        "Chunk store mapping key mismatch: mapping key "
                        f"{chunk_id!r} does not match record chunk_id "
                        f"{normalized_chunk_id!r}."
                    )
                mapping[normalized_chunk_id] = normalized_record
        else:
            for chunk in chunks:
                normalized_record = _normalize_chunk_record(chunk)
                if normalized_record is None:
                    chunk_context_str = ""
                    if isinstance(chunk, Mapping):
                        chunk_id_ctx = chunk.get("chunk_id")
                        if isinstance(chunk_id_ctx, str) and chunk_id_ctx:
                            chunk_context_str = chunk_id_ctx
                        else:
                            chunk_context_str = repr(dict(chunk))
                    else:
                        chunk_context_str = repr(chunk)
                    raise RuntimeError(
                        "Invalid chunk record in iterable input: "
                        f"{chunk_context_str}. Record must include chunk_id "
                        "(non-empty string) and text (string)."
                    )
                normalized_chunk_id = str(normalized_record.get("chunk_id"))
                mapping[normalized_chunk_id] = normalized_record
        _chunks_by_id = mapping
        _bm25_index = _Bm25Index(mapping) if mapping else None


def warm_chunk_store() -> bool:
    """
    Force-load the chunk side store so Cloud Run knows whether it is ready.
    Returns True if any chunks were loaded.

    Inputs:
        None.
    Outputs:
        True when at least one chunk is loaded.
    Edge cases:
        Returns False when no chunks are available.
    """
    _ensure_chunk_store_loaded()
    size = len(_chunks_by_id or {})
    logger.info(" Loaded %d persona chunks into memory", size)
    return bool(size)


def _freeze_snapshot_value(value: Any) -> Any:
    """
    Recursively freeze a Python value into immutable containers.

    Inputs:
        value: Arbitrary JSON-like object.
    Outputs:
        Immutable clone: mapping -> MappingProxyType, list/tuple -> tuple, set -> frozenset.
    Edge cases:
        Scalars are returned unchanged.
    """
    if isinstance(value, Mapping):
        frozen_mapping = {
            key: _freeze_snapshot_value(inner_value) for key, inner_value in value.items()
        }
        return MappingProxyType(frozen_mapping)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_snapshot_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_snapshot_value(item) for item in value)
    return value


def get_chunk_store_snapshot() -> Mapping[str, Mapping[str, Any]]:
    """
    Return an immutable snapshot of the currently loaded chunk store.

    Inputs:
        None.
    Outputs:
        Mapping from chunk id to immutable chunk records.
    Edge cases:
        Returns an empty mapping when no chunks are loaded.
    Concurrency:
        Snapshot creation is protected by _chunk_lock.
    """
    _ensure_chunk_store_loaded()
    with _chunk_lock:
        chunks = _chunks_by_id or {}
        frozen_chunks: Dict[str, Mapping[str, Any]] = {}
        for chunk_id, chunk_record in chunks.items():
            frozen_chunk = _freeze_snapshot_value(chunk_record)
            if isinstance(frozen_chunk, Mapping):
                frozen_chunks[chunk_id] = frozen_chunk
        return MappingProxyType(frozen_chunks)


def _resolve_local_chunks_path(name: str) -> Optional[Path]:
    """
    Resolve a chunk file path using configured search roots.

    Inputs:
        name: File path or basename to resolve.
    Outputs:
        A resolved Path if a local file exists, otherwise None.
    Edge cases:
        Searches PRIVATE_DIR and repo paths when a direct file match fails.
    """
    candidate = Path(name)
    if candidate.is_file():
        return candidate

    search_roots: List[Path] = []
    private_dir = os.getenv("PRIVATE_DIR")
    if private_dir:
        base = Path(private_dir).expanduser()
        search_roots.extend(
            [
                base,
                base / "persona",
                base / "persona" / "data",
                base / "persona" / "assets",
            ]
        )
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    search_roots.extend([backend_root, repo_root])

    for root in search_roots:
        candidate = (root / name).resolve()
        if candidate.is_file():
            return candidate
    return None


def _iter_chunk_records() -> Iterable[Dict[str, Any]]:
    """
    Yield chunk records from local disk or GCS for backward compatibility.

    Inputs:
        None. Uses settings.CHUNKS_PATH and settings.BUCKET_NAME.
    Outputs:
        An iterator of chunk dictionaries loaded from storage.
    Edge cases:
        Supports gzip-compressed files and raises on invalid GCS URIs.
    """
    path_value = settings.CHUNKS_PATH or ""
    if not path_value:
        return
    local_path = _resolve_local_chunks_path(path_value)
    if local_path:
        opener = gzip.open if local_path.suffix.endswith(".gz") else open
        with opener(local_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
        return

    # Fallback to GCS using the configured bucket.
    object_path = path_value.lstrip("/")
    bucket_name = settings.BUCKET_NAME
    uri = path_value if path_value.startswith("gs://") else f"gs://{bucket_name}/{object_path}"
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("google-cloud-storage is required to load chunk side store") from exc

    match = re.match(r"gs://([^/]+)/(.+)", uri)
    if not match:
        raise RuntimeError(f"Invalid GCS URI for chunks: {uri}")

    bucket_id, blob_name = match.groups()
    client: Any = storage.Client()
    bucket: Any = client.bucket(bucket_id)
    blob: Any = bucket.blob(blob_name)
    data: bytes = blob.download_as_bytes(timeout=settings.request_timeout_seconds)

    is_gzip = blob_name.endswith(".gz")
    stream = io.BytesIO(data)
    handle = gzip.open(stream, "rt", encoding="utf-8") if is_gzip else io.TextIOWrapper(stream, encoding="utf-8")
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _ensure_chunk_store_loaded() -> None:
    """
    Ensure the chunk store is loaded before applying filters.

    Inputs:
        None.
    Outputs:
        None. Populates module-level chunk store and BM25 index.
    Edge cases:
        Falls back to CHUNKS_PATH when dataset cache is unavailable.
    Concurrency:
        Uses _chunk_lock to avoid concurrent loads across threads.
    """
    global _chunks_by_id, _bm25_index
    if _chunks_by_id is not None:
        return

    with _chunk_lock:
        if _chunks_by_id is not None:
            return
        try:
            cache = dataset_cache.get_or_load_cache()
            configure_chunk_store(cache.chunks_by_id)
            return
        except Exception as exc:
            if (settings.DATASET_POINTER_PATH or "").strip():
                raise RuntimeError(
                    "Failed to load dataset cache while DATASET_POINTER_PATH is configured"
                ) from exc
            logger.exception(
                "Failed to load dataset cache for chunks; falling back to CHUNKS_PATH."
            )
        records: Dict[str, Dict[str, Any]] = {}
        for record in _iter_chunk_records():
            normalized_record = _normalize_chunk_record(record)
            if normalized_record is None:
                continue
            chunk_id = str(normalized_record["chunk_id"])
            records[chunk_id] = normalized_record

        _chunks_by_id = records
        _bm25_index = _Bm25Index(records) if records else None


def apply_filters_and_boosting(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply hybrid scoring that weights ANN distance, BM25 lexical signal, and field/profile/topic boosts.

    Inputs:
        candidates: Vector search results, each with at least chunk_id and distance.
    Outputs:
        A ranked list of chunk records enriched with score metadata.
    Edge cases:
        Returns an empty list when candidates are empty or chunk store is missing.
    Concurrency:
        Uses ContextVar for per-request query context.
    """
    if not candidates:
        return []

    _ensure_chunk_store_loaded()
    chunks = _chunks_by_id or {}
    if not chunks:
        return []

    question = _CURRENT_QUERY.get("")
    bm25_scores = (
        _bm25_index.score(_tokenize_for_bm25(question)) if _bm25_index and question else {}
    )
    profile_hint = _classify_query_profile(question)
    topic_tokens = set(_tokenize(question))
    vector_weight = float(settings.RETRIEVAL_VECTOR_WEIGHT)
    bm25_weight = float(settings.RETRIEVAL_BM25_WEIGHT)

    ranked: List[Dict[str, Any]] = []
    for candidate in candidates:
        if "chunk_id" not in candidate:
            continue
        chunk_id_value = candidate["chunk_id"]
        if not isinstance(chunk_id_value, str) or not chunk_id_value:
            continue
        chunk_id = chunk_id_value
        chunk = chunks.get(chunk_id)
        if not chunk:
            continue

        distance = float(candidate.get("distance", 0.0))
        vector_score = _distance_to_similarity(distance)
        bm25_raw = bm25_scores.get(chunk_id, 0.0)
        weighted = vector_weight * vector_score + bm25_weight * _normalize_bm25(
            bm25_raw
        )

        profile = _chunk_profile(chunk)
        if profile_hint and profile and profile == profile_hint:
            weighted += _PROFILE_BOOST

        if topic_tokens:
            matches = len(_chunk_topics(chunk) & topic_tokens)
            if matches:
                weighted += min(_TOPIC_BOOST * matches, _MAX_TOPIC_BOOST)

        ranked_record = dict(chunk)
        ranked_record["chunk_id"] = chunk_id
        ranked_record["distance"] = distance
        ranked_record["vector_score"] = vector_score
        ranked_record["bm25_score"] = bm25_raw
        ranked_record["score"] = weighted
        ranked.append(ranked_record)

    ranked.sort(key=lambda item: item["score"], reverse=True)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            {
                "event": "retrieval.bm25_debug",
                "query": question,
                "vector_weight": vector_weight,
                "bm25_weight": bm25_weight,
                "candidates": [
                    {
                        "chunk_id": item["chunk_id"],
                        "bm25_score": item["bm25_score"],
                        "vector_score": item["vector_score"],
                        "score": item["score"],
                    }
                    for item in ranked[:_DEBUG_BM25_SAMPLE]
                ],
            }
        )
    return ranked[:_MAX_CONTEXT_CHUNKS]


def build_context_prompt(question: str, selected: List[Dict[str, Any]]) -> str:
    """
    Build a prompt string from the question and selected context chunks.

    Inputs:
        question: Raw question text.
        selected: Ranked chunk records to include.
    Outputs:
        A formatted prompt string with question and chunk sections.
    Edge cases:
        Returns an empty string when both inputs are empty.
    """
    question = (question or "").strip()
    sections: List[str] = []
    if question:
        sections.append(f"{QUESTION_PREFIX}{question}")

    for idx, chunk in enumerate(selected, start=1):
        text = (chunk.get("text") or "").strip()
        if not text:
            continue

        label_parts: List[str] = []
        profile = _chunk_profile(chunk)
        if profile:
            label_parts.append(profile)
        section = chunk.get("section")
        if isinstance(section, str) and section:
            label_parts.append(section)
        extras_obj = chunk.get("extras")
        if isinstance(extras_obj, Mapping):
            extras_map = cast(Mapping[str, Any], extras_obj)
            employer_value = extras_map.get("employer")
            if isinstance(employer_value, str) and employer_value:
                label_parts.append(employer_value)

        header = f"[{idx}]"
        if label_parts:
            header = f"{header} {' | '.join(label_parts)}"

        score = chunk.get("score")
        if isinstance(score, (float, int)):
            header = f"{header} (score={float(score):.3f})"

        sections.append(f"{header}\n{text}")

    return "\n\n".join(sections)


def has_selected_chunks(selected: List[Dict[str, Any]]) -> bool:
    """
    Decide whether any context signals are present.

    Inputs:
        selected: Ranked chunk records.
    Outputs:
        True when any chunks are selected, otherwise False.
    Edge cases:
        Returns False for empty inputs.
    """
    return bool(selected)


class _GenaiEmbeddingClient:
    """Lazy google-genai embedding wrapper (Vertex mode)."""

    def __init__(self, *, project: str, region: str, model_name: str) -> None:
        self._project = project
        self._region = region
        self._model_name = model_name
        self._client: Any = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> Any:
        """
        Initialize and cache a google-genai client.

        Inputs:
            None.
        Outputs:
            A genai.Client instance configured for Vertex mode.
        Edge cases:
            Lazily initializes to avoid import-time failures.
        Concurrency:
            Protected by a lock to ensure single initialization.
        """
        with self._lock:
            if self._client is None:
                from google import genai  # type: ignore[import-not-found]
                from google.genai import types  # type: ignore[import-not-found]

                http_options = types.HttpOptions(timeout=settings.REQ_TIMEOUT_MS)
                self._client = genai.Client(
                    vertexai=True,
                    project=self._project,
                    location=self._region,
                    http_options=http_options,
                )
            return self._client

    def embed(self, text: str) -> Optional[Sequence[float]]:
        """
        Embed text using the configured model via google-genai.

        Inputs:
            text: Raw text to embed.
        Outputs:
            A sequence of floats, or an empty list if the backend returns no values.
        Edge cases:
            Raises RuntimeError if the response lacks an embedding payload.
        """
        normalized_text = (text or "").strip()
        if not normalized_text:
            return cast(List[float], [])

        from google.genai import types  # type: ignore[import-not-found]

        client = self._ensure_client()
        response = client.models.embed_content(
            model=self._model_name,
            contents=[types.Part.from_text(text=normalized_text)],
            config=types.EmbedContentConfig(auto_truncate=True),
        )
        response_object = cast(object, response)
        embeddings_attribute = getattr(response_object, "embeddings", None)
        embeddings = cast(Optional[Sequence[Any]], embeddings_attribute)
        if not embeddings:
            return cast(List[float], [])

        first_embedding_object = cast(object, embeddings[0])
        values = cast(Optional[Iterable[Any]], getattr(first_embedding_object, "values", None))
        if values is None:
            raise RuntimeError("Embedding response missing values field")
        return [float(value) for value in values]
