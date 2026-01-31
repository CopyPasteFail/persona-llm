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
_ROLE_BOOST = 0.05
_TOPIC_BOOST = 0.02
_MAX_TOPIC_BOOST = 0.06
_VECTOR_WEIGHT = 0.7
_BM25_WEIGHT = 0.3
_DEBUG_NEIGHBOR_SAMPLE = 5

# BM25 parameters.
_BM25_K1 = 1.5
_BM25_B = 0.75

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


def _chunk_role(metadata: Mapping[str, Any]) -> Optional[str]:
    """
    Extract a role label from chunk metadata.

    Inputs:
        metadata: Metadata mapping for a chunk.
    Outputs:
        A lowercase role string, or None if not present.
    Edge cases:
        Supports both "role" field and "role:" tag prefixes.
    """
    role = metadata.get("role")
    if isinstance(role, str) and role:
        return role.lower()

    tags = metadata.get("tags")
    if isinstance(tags, Iterable):
        for tag in cast(Iterable[Any], tags):
            if isinstance(tag, str) and tag.startswith("role:"):
                return tag.split(":", 1)[1].lower()
    return None


def _chunk_topics(metadata: Mapping[str, Any]) -> Set[str]:
    """
    Extract topic labels from chunk metadata.

    Inputs:
        metadata: Metadata mapping for a chunk.
    Outputs:
        A set of lowercase topic strings.
    Edge cases:
        Supports both "topics" field and "topic:" tag prefixes.
    """
    topics: Set[str] = set()
    raw_topics = metadata.get("topics")
    if isinstance(raw_topics, Iterable):
        for topic in cast(Iterable[Any], raw_topics):
            if isinstance(topic, str):
                topics.add(topic.lower())
    tags = metadata.get("tags")
    if isinstance(tags, Iterable):
        for tag in cast(Iterable[Any], tags):
            if isinstance(tag, str) and tag.startswith("topic:"):
                topics.add(tag.split(":", 1)[1].lower())
    return topics


def _classify_query_role(question: str) -> Optional[str]:
    """
    Classify a query as infra or product based on keyword hints.

    Inputs:
        question: Raw user question.
    Outputs:
        "infra", "product", or None if ambiguous.
    Edge cases:
        Returns None when tokens overlap or are empty.
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
    Extract searchable tokens from chunk text and metadata.

    Inputs:
        chunk: Chunk record containing text and metadata.
    Outputs:
        A list of tokens from text, section, doc_id, source_uri, topics, tags, and extras.
    Edge cases:
        Skips non-string fields and missing metadata entries.
    """
    tokens: List[str] = []
    text = chunk.get("text")
    if isinstance(text, str):
        tokens.extend(_tokenize(text))

    metadata_obj = chunk.get("metadata")
    if isinstance(metadata_obj, Mapping):
        metadata_mapping = cast(Mapping[str, Any], metadata_obj)
        metadata_map: Dict[str, Any] = dict(metadata_mapping)
        for field in ("section", "doc_id", "source_uri"):
            value = metadata_map.get(field)
            if isinstance(value, str):
                tokens.extend(_tokenize(value))

        topics = metadata_map.get("topics")
        if isinstance(topics, Iterable):
            for topic in cast(Iterable[Any], topics):
                if isinstance(topic, str):
                    tokens.extend(_tokenize(topic))

        tags = metadata_map.get("tags")
        if isinstance(tags, Iterable):
            for tag in cast(Iterable[Any], tags):
                if isinstance(tag, str):
                    tokens.extend(_tokenize(tag))

        extras = metadata_map.get("extras")
        if isinstance(extras, Mapping):
            for raw_value in cast(Iterable[Any], extras.values()):
                if isinstance(raw_value, str):
                    tokens.extend(_tokenize(raw_value))
                elif isinstance(raw_value, Iterable):
                    for item in cast(Iterable[Any], raw_value):
                        if isinstance(item, str):
                            tokens.extend(_tokenize(item))
    return tokens


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
                text = chunk.get("text")
                if not chunk_id or not isinstance(text, str):
                    continue
                metadata_obj = chunk.get("metadata")
                metadata_dict: Dict[str, Any] = {}
                if isinstance(metadata_obj, Mapping):
                    metadata_dict = dict(cast(Mapping[str, Any], metadata_obj))
                mapping[chunk_id] = {
                    "id": chunk_id,
                    "text": text,
                    "metadata": metadata_dict,
                }
        else:
            for chunk in chunks:
                chunk_id = str(chunk.get("id") or "")
                text = chunk.get("text")
                if not chunk_id or not isinstance(text, str):
                    continue
                metadata_obj = chunk.get("metadata")
                metadata_dict: Dict[str, Any] = {}
                if isinstance(metadata_obj, Mapping):
                    metadata_dict = dict(cast(Mapping[str, Any], metadata_obj))
                record: Dict[str, Any] = {
                    "id": chunk_id,
                    "text": text,
                    "metadata": metadata_dict,
                }
                mapping[chunk_id] = record
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
            chunk_id = str(record.get("id") or "")
            text = record.get("text")
            if not chunk_id or not isinstance(text, str):
                continue
            metadata_obj = record.get("metadata")
            metadata: Dict[str, Any] = {}
            if isinstance(metadata_obj, Mapping):
                metadata = dict(cast(Mapping[str, Any], metadata_obj))
            record_clean: Dict[str, Any] = {"id": chunk_id, "text": text, "metadata": metadata}
            records[chunk_id] = record_clean

        _chunks_by_id = records
        _bm25_index = _Bm25Index(records) if records else None


def apply_filters_and_boosting(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply hybrid scoring that blends ANN distance, BM25 lexical signal, and metadata boosts.

    Inputs:
        candidates: Vector search results, each with at least id and distance.
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
    bm25_scores = _bm25_index.score(_tokenize(question)) if _bm25_index and question else {}
    role_hint = _classify_query_role(question)
    topic_tokens = set(_tokenize(question))

    ranked: List[Dict[str, Any]] = []
    for candidate in candidates:
        chunk_id = str(candidate.get("id") or "")
        if not chunk_id:
            continue
        chunk = chunks.get(chunk_id)
        if not chunk:
            continue

        distance = float(candidate.get("distance", 0.0))
        vector_score = _distance_to_similarity(distance)
        bm25_raw = bm25_scores.get(chunk_id, 0.0)
        blended = _VECTOR_WEIGHT * vector_score + _BM25_WEIGHT * _normalize_bm25(
            bm25_raw
        )

        metadata = cast(Dict[str, Any], chunk.get("metadata") or {})
        role = _chunk_role(metadata)
        if role_hint and role and role == role_hint:
            blended += _ROLE_BOOST

        if topic_tokens:
            matches = len(_chunk_topics(metadata) & topic_tokens)
            if matches:
                blended += min(_TOPIC_BOOST * matches, _MAX_TOPIC_BOOST)

        ranked.append(
            {
                "id": chunk_id,
                "text": chunk["text"],
                "metadata": metadata,
                "distance": distance,
                "vector_score": vector_score,
                "bm25_score": bm25_raw,
                "score": blended,
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
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

        metadata = cast(Dict[str, Any], chunk.get("metadata") or {})
        label_parts: List[str] = []
        role = _chunk_role(metadata)
        if role:
            label_parts.append(role)
        section = metadata.get("section")
        if isinstance(section, str) and section:
            label_parts.append(section)
        extras_obj = metadata.get("extras")
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


def has_signal(selected: List[Dict[str, Any]]) -> bool:
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
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            return cast(List[float], [])
        values = getattr(embeddings[0], "values", None)
        if values is None:
            raise RuntimeError("Embedding response missing values field")
        return [float(value) for value in cast(Iterable[Any], values)]
