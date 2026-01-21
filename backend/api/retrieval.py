"""
Retrieval helpers. Keep pure and side-effect free.

Name normalization uses the configured persona name from settings.PERSONA_NAME.
It generates regexes for *all permutations* of up to 4 name parts. For example:
  "Alex Taylor" -> ["Alex", "Taylor", "Alex Taylor", "Taylor Alex"]

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

logger = logging.getLogger(" api.retrieval")
if os.getenv("RETRIEVAL_DEBUG") == "1":
    logger.setLevel(logging.DEBUG)


def _persona_variants() -> list[str]:
    """
    Generate all permutations of all non-empty subsets of the persona name parts.
    Example: "Alex Taylor" -> ["Alex", "Taylor", "Alex Taylor", "Taylor Alex"].
    Enforced max 4 words in settings prevents combinatorial explosion.
    """
    full = (settings.PERSONA_NAME or "").strip()
    if not full:
        return []

    parts = full.split()
    variants: list[str] = []
    n = len(parts)
    for r in range(1, n + 1):  # subset size
        for combo in itertools.permutations(parts, r):
            variants.append(" ".join(combo))

    # de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _compile_regexes():
    """
    Build regexes on demand using the current PERSONA_NAME from settings.
    Returns (possessive_regex, bare_regex).
    """
    variants = _persona_variants()
    if not variants:
        return None, None

    # IMPORTANT: longest-first so "Alex Taylor" beats "Alex" then "Taylor"
    variants = sorted(variants, key=len, reverse=True)

    alt = "|".join(re.escape(v) for v in variants)
    group = fr"(?:{alt})"

    possessive = re.compile(
        rf"(?<![\w.+-/]){group}{_APOS}s\b(?!@)",
        re.IGNORECASE | re.UNICODE,
    )

    bare = re.compile(
        rf"(?<![@/\w]){group}\b(?!{_APOS}s\w)(?!@)",
        re.IGNORECASE | re.UNICODE,
    )

    return possessive, bare


def normalize_question_for_first_person(q: str) -> str:
    """
    Convert references like "<Name>'s" -> "your" and "<Name>" -> "I",
    while avoiding emails, usernames and inside-word matches.

    Regexes are compiled lazily from settings.PERSONA_NAME so tests
    can inject env vars without being broken by early imports.
    """
    if not q:
        return q

    possessive, bare = _compile_regexes()
    if not possessive or not bare:
        return q

    out = possessive.sub("your", q)
    out = bare.sub("I", out)
    return out


# ---- Real integrations (unimplemented in mock) ----

_CURRENT_QUERY: ContextVar[str] = ContextVar("_current_query", default="")


@runtime_checkable
class _EmbeddingClient(Protocol):
    def embed(self, text: str) -> Optional[Sequence[float]]:
        ...


_embedding_client: Optional[_EmbeddingClient] = None


def configure_embedding_client(client: Optional[_EmbeddingClient]) -> None:
    """Override the embedding client (facilitates tests)."""
    global _embedding_client
    _embedding_client = client


def _get_embedding_client() -> _EmbeddingClient:
    global _embedding_client
    if _embedding_client is None:
        model_name = (
            os.getenv("EMBEDDING_MODEL")
            or os.getenv("DATAPOINTS_MODEL")
            or "text-embedding-004"
        )
        _embedding_client = _VertexEmbeddingClient(
            project=settings.PROJECT_ID,
            region=settings.REGION,
            model_name=model_name,
        )
    return _embedding_client


def embed_query(question: str) -> Optional[List[float]]:
    """
    Embed the normalized question using Vertex AI Text Embedding model.

    The question text is stored in a ContextVar so downstream ranking logic can
    access it without threading issues (FastAPI async requests).
    """
    normalized_question = (question or "").strip()
    _CURRENT_QUERY.set(normalized_question)
    if not normalized_question:
        return None

    client = _get_embedding_client()
    try:
        raw_vector = client.embed(normalized_question)
    except Exception as exc:
        raise RuntimeError("Failed to embed query") from exc

    if not raw_vector:
        return None

    return [float(x) for x in raw_vector]

@runtime_checkable
class _VectorSearchClient(Protocol):
    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        ...


_vector_client: Optional[_VectorSearchClient] = None


def configure_vector_client(client: Optional[_VectorSearchClient]) -> None:
    """Override the underlying vector search client (primarily used in tests)."""
    global _vector_client
    _vector_client = client


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    """Return a unit-normalized copy of the provided vector."""
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm == 0:
        return [float(x) for x in vector]
    scale = 1.0 / norm
    return [float(x) * scale for x in vector]


def _get_vector_client() -> _VectorSearchClient:
    global _vector_client
    if _vector_client is None:
        _vector_client = vector_backends.get_vector_backend()
    return _vector_client


def search_vector_store(embedding: Optional[Sequence[float]], top_k: int = 8) -> List[Dict[str, Any]]:
    """Normalize and query the vector backend, returning candidate neighbors."""
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
        [float(n.get("distance", 0.0)) for n in neighbors[:5]],
    )
    return neighbors

_MAX_CONTEXT_CHUNKS = 8
_ROLE_BOOST = 0.05
_TOPIC_BOOST = 0.02

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
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _distance_to_similarity(distance: float) -> float:
    if math.isnan(distance):
        return 0.0
    if distance < 0:
        distance = abs(distance)
    return 1.0 / (1.0 + distance)


def _normalize_bm25(score: float) -> float:
    if score <= 0:
        return 0.0
    return score / (score + 1.0)


def _chunk_role(metadata: Mapping[str, Any]) -> Optional[str]:
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
        self._k1 = 1.5
        self._b = 0.75

    def score(self, query_tokens: Iterable[str]) -> Dict[str, float]:
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
                denom = tf + self._k1 * (1 - self._b + self._b * (doc_len / (self._avg_len or 1.0)))
                scores[chunk_id] += idf * (tf * (self._k1 + 1)) / denom
        return dict(scores)


_chunk_lock = threading.Lock()
_chunks_by_id: Optional[Dict[str, Dict[str, Any]]] = None
_bm25_index: Optional[_Bm25Index] = None


def configure_chunk_store(
    chunks: Optional[Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]]]
) -> None:
    """Allow tests or callers to inject a deterministic chunk corpus."""
    global _chunks_by_id, _bm25_index
    with _chunk_lock:
        if chunks is None:
            _chunks_by_id = None
            _bm25_index = None
            return

        mapping: Dict[str, Dict[str, Any]] = {}
        if isinstance(chunks, Mapping):
            for chunk_id, chunk in chunks.items():
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
    """
    _ensure_chunk_store_loaded()
    size = len(_chunks_by_id or {})
    logger.info(" Loaded %d persona chunks into memory", size)
    return bool(size)

def _resolve_local_chunks_path(name: str) -> Optional[Path]:
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
    """Yield chunk records from local disk or GCS for backward compatibility."""
    path_value = settings.CHUNKS_PATH or ""
    if not path_value:
        return []
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
    """Ensure the chunk store is loaded before applying filters."""
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
    """Hybrid scoring that blends ANN distance, BM25 lexical signal, and soft metadata boosts."""
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
        blended = 0.7 * vector_score + 0.3 * _normalize_bm25(bm25_raw)

        metadata = cast(Dict[str, Any], chunk.get("metadata") or {})
        role = _chunk_role(metadata)
        if role_hint and role and role == role_hint:
            blended += _ROLE_BOOST

        if topic_tokens:
            matches = len(_chunk_topics(metadata) & topic_tokens)
            if matches:
                blended += min(_TOPIC_BOOST * matches, 0.06)

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
    """Helper used by main.py mock/integrated path to decide if we answer at all."""
    return bool(selected)


class _VertexEmbeddingClient:
    """Lazy Vertex AI Text Embedding wrapper."""

    def __init__(self, *, project: str, region: str, model_name: str) -> None:
        self._project = project
        self._region = region
        self._model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> Any:
        with self._lock:
            if self._model is None:
                from vertexai import init  # type: ignore[import-not-found]
                from vertexai.language_models import TextEmbeddingModel  # type: ignore[import-not-found]

                init(project=self._project, location=self._region)
                self._model = TextEmbeddingModel.from_pretrained(self._model_name)
            return self._model

    def embed(self, text: str) -> Optional[Sequence[float]]:
        model = self._ensure_model()
        responses = model.get_embeddings([text], auto_truncate=True)
        if not responses:
            return cast(List[float], [])
        embedding = responses[0]
        for attr in ("values", "embedding", "embedding_values"):
            values = getattr(embedding, attr, None)
            if values is not None:
                return [float(v) for v in cast(Iterable[Any], values)]
        raise RuntimeError("Embedding response missing values field")
