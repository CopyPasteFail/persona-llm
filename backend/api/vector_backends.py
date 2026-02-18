from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Sequence, cast, runtime_checkable

from . import dataset_cache
from .settings import settings

_NEGATIVE_SIMILARITY_DISTANCE = 1e6
_SIMILARITY_EPSILON = 1e-6


@runtime_checkable
class VectorBackend(Protocol):
    """Protocol for vector search backends."""

    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        """Return vector search candidates for a normalized embedding."""
        ...


class LocalVectorBackend:
    """In-process cosine search over the cached dataset embeddings."""

    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        """Return top-k candidates using cosine similarity on normalized vectors."""
        cache = dataset_cache.get_or_load_cache()
        if cache.dimensions != len(embedding):
            raise RuntimeError(
                "Query embedding dimension mismatch: "
                f"expected={cache.dimensions} got={len(embedding)}"
            )

        scores: List[tuple[float, str]] = []
        for idx, vector in enumerate(cache.embeddings):
            similarity = _dot_product(vector, embedding)
            scores.append((similarity, cache.ids[idx]))

        scores.sort(key=lambda item: item[0], reverse=True)
        top_scores = scores[: min(top_k, len(scores))]

        candidates: List[Dict[str, Any]] = []
        for similarity, datapoint_id in top_scores:
            distance = _distance_from_similarity(similarity)
            candidates.append({"chunk_id": datapoint_id, "distance": distance})
        return candidates


class MatchingEngineBackend:
    """Lazy wrapper around Vertex AI Matching Engine."""

    def __init__(self, *, index_endpoint_path: str, deployed_index_id: str) -> None:
        self._index_endpoint_path = index_endpoint_path
        self._deployed_index_id = deployed_index_id
        self._endpoint = None

    def _ensure_endpoint(self):
        """Initialize the Matching Engine endpoint client lazily."""
        if self._endpoint is None:
            from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import (
                MatchingEngineIndexEndpoint,
            )

            self._endpoint = MatchingEngineIndexEndpoint(
                index_endpoint_name=self._index_endpoint_path
            )
        return self._endpoint

    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        """Query Matching Engine and return candidates in the expected shape."""
        endpoint = self._ensure_endpoint()
        find_neighbors = cast(Any, endpoint).find_neighbors
        try:
            responses = cast(
                Sequence[Any],
                find_neighbors(
                    deployed_index_id=self._deployed_index_id,
                    queries=[list(embedding)],
                    num_neighbors=top_k,
                    timeout=settings.request_timeout_seconds,
                ),
            )
        except TypeError:
            responses = cast(
                Sequence[Any],
                find_neighbors(
                    deployed_index_id=self._deployed_index_id,
                    queries=[list(embedding)],
                    num_neighbors=top_k,
                ),
            )

        if not responses:
            return []

        response = responses[0]
        neighbors = cast(Sequence[Any], getattr(response, "neighbors", []))
        if not neighbors:
            return []

        return [_neighbor_to_candidate(neighbor) for neighbor in neighbors]


_backend: Optional[VectorBackend] = None


def get_vector_backend() -> VectorBackend:
    """Return a cached vector backend based on VECTOR_BACKEND setting."""
    global _backend
    if _backend is not None:
        return _backend

    backend_name = (settings.VECTOR_BACKEND or "local").strip().lower()
    if backend_name == "matching_engine":
        if not settings.INDEX_ENDPOINT_ID or not settings.DEPLOYED_INDEX_ID:
            raise RuntimeError(
                "VECTOR_BACKEND=matching_engine requires INDEX_ENDPOINT_ID and DEPLOYED_INDEX_ID"
            )
        _backend = MatchingEngineBackend(
            index_endpoint_path=settings.index_endpoint_path,
            deployed_index_id=settings.DEPLOYED_INDEX_ID,
        )
        return _backend
    if backend_name == "local":
        _backend = LocalVectorBackend()
        return _backend

    raise RuntimeError(f"Unknown VECTOR_BACKEND: {backend_name}")


def _distance_from_similarity(similarity: float) -> float:
    """Convert cosine similarity to a distance compatible with scoring."""
    if similarity <= 0.0:
        return _NEGATIVE_SIMILARITY_DISTANCE
    safe_similarity = min(similarity, 1.0)
    return (1.0 - safe_similarity) / max(safe_similarity, _SIMILARITY_EPSILON)


def _dot_product(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute dot product for two same-length vectors."""
    return sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))


def _neighbor_proto(neighbor: Any) -> Any:
    """Extract the underlying proto from a Matching Engine neighbor."""
    to_proto = getattr(neighbor, "to_proto", None)
    if callable(to_proto):
        proto = to_proto()
        if proto is not None:
            return proto

    for attr in ("proto", "_proto", "_pb"):
        proto = getattr(neighbor, attr, None)
        if proto is not None:
            return proto

    raise AttributeError("Matching Engine neighbor lacks a proto representation")


def _neighbor_to_candidate(neighbor: Any) -> Dict[str, Any]:
    """Convert a Matching Engine neighbor proto into a simplified dict."""
    try:
        from google.protobuf.json_format import MessageToDict
    except ImportError:
        MessageToDict = None

    if MessageToDict is None:
        raise RuntimeError(
            "google-cloud-aiplatform dependency is required for live vector search"
        )

    neighbor_dict = MessageToDict(_neighbor_proto(neighbor), preserving_proto_field_name=True)
    datapoint = neighbor_dict.get("datapoint", {})

    candidate: Dict[str, Any] = {
        "chunk_id": datapoint.get("datapointId")
        or neighbor_dict.get("datapointId")
        or neighbor_dict.get("id"),
        "distance": float(neighbor_dict.get("distance", 0.0)),
    }

    feature_vector = datapoint.get("featureVector")
    if feature_vector is not None:
        candidate["featureVector"] = [float(x) for x in feature_vector]

    restricts = datapoint.get("restricts")
    if restricts:
        candidate["restricts"] = restricts

    crowding_tag = datapoint.get("crowdingTag")
    if crowding_tag:
        candidate["crowdingTag"] = crowding_tag

    return candidate
