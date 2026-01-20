from __future__ import annotations

import gzip
import io
import json
import math
import os
import threading
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .settings import settings

_DATASET_FOLDER = "datasets"
_DATASET_POINTER_PATH = f"{_DATASET_FOLDER}/current.json"
_DATASET_POINTER_KEY = "version"
_MANIFEST_REQUIRED_KEYS = {
    "version",
    "created_at",
    "datapoints_file",
    "chunks_file",
    "embedding_model",
    "dimensions",
    "num_datapoints",
}

_EMBEDDING_MODEL_ENV_KEYS = ("EMBEDDING_MODEL", "DATAPOINTS_MODEL")
_DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
_NORM_TOLERANCE = 0.15
_NORM_MIN = 1.0 - _NORM_TOLERANCE
_NORM_MAX = 1.0 + _NORM_TOLERANCE

_CHUNKS_FILENAME = "chunks.jsonl.gz"
_DATAPOINTS_FILENAME = "datapoints.jsonl"
_MANIFEST_FILENAME = "manifest.json"

_cache_lock = threading.Lock()
_dataset_cache: Optional["DatasetCache"] = None


@dataclass(frozen=True)
class DatasetCache:
    """In-memory dataset snapshot built from a versioned GCS folder."""

    version: str
    loaded_at: str
    embedding_model: str
    dimensions: int
    num_datapoints: int
    datapoints_generation: Optional[str]
    chunks_generation: Optional[str]
    manifest_generation: Optional[str]
    ids: List[str]
    embeddings: List[Sequence[float]]
    chunks_by_id: Dict[str, Dict[str, Any]]


@dataclass(frozen=True)
class PointerInfo:
    """Dataset pointer info from GCS."""

    version: str
    generation: Optional[str]


def get_cache_snapshot() -> Optional[DatasetCache]:
    """Return the current dataset cache without forcing a reload."""
    with _cache_lock:
        return _dataset_cache


def get_or_load_cache() -> DatasetCache:
    """Return the current cache, loading it from the pointer if needed."""
    cached = get_cache_snapshot()
    if cached is not None:
        return cached
    return reload_cache()


def reload_cache() -> DatasetCache:
    """Reload the dataset cache from the pointer file and swap it atomically.

    Concurrency:
        Builds the new cache without holding the swap lock, then replaces the
        global pointer under a lock to avoid partial state.
    """
    cache = _load_cache_from_pointer()
    with _cache_lock:
        global _dataset_cache
        _dataset_cache = cache
    return cache


def get_pointer_info() -> PointerInfo:
    """Load the dataset pointer JSON and return the version."""
    pointer_path = _DATASET_POINTER_PATH
    bucket_name = settings.BUCKET_NAME
    data, generation = _read_gcs_object(bucket_name, pointer_path)
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Pointer file is not valid JSON: {pointer_path}") from exc

    version = payload.get(_DATASET_POINTER_KEY)
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError(f"Pointer file missing '{_DATASET_POINTER_KEY}': {pointer_path}")
    return PointerInfo(version=version.strip(), generation=generation)


def _load_cache_from_pointer() -> DatasetCache:
    """Resolve the pointer to a dataset version and load its artifacts."""
    pointer_info = get_pointer_info()
    version = pointer_info.version

    version_prefix = _DATASET_FOLDER
    base_prefix = f"{version_prefix}/{version}/"

    manifest_path = f"{base_prefix}{_MANIFEST_FILENAME}"
    datapoints_path = f"{base_prefix}{_DATAPOINTS_FILENAME}"
    chunks_path = f"{base_prefix}{_CHUNKS_FILENAME}"

    manifest_data, manifest_generation = _read_gcs_object(settings.BUCKET_NAME, manifest_path)
    manifest = _parse_manifest(
        manifest_data,
        manifest_path=manifest_path,
        expected_version=version,
    )

    _validate_manifest_filenames(manifest)
    expected_model = _expected_embedding_model()
    _validate_manifest_model(manifest, expected_model)
    _validate_manifest_dimensions(manifest)

    chunks_data, chunks_generation = _read_gcs_object(settings.BUCKET_NAME, chunks_path)
    chunks_by_id = _load_chunks(chunks_data, chunks_path)

    datapoints_data, datapoints_generation = _read_gcs_object(
        settings.BUCKET_NAME, datapoints_path
    )
    ids, embeddings = _load_datapoints(
        datapoints_data,
        datapoints_path,
        chunks_by_id=chunks_by_id,
        manifest_dimensions=int(manifest["dimensions"]),
    )

    num_datapoints = len(ids)
    if num_datapoints != int(manifest["num_datapoints"]):
        raise RuntimeError(
            "Manifest num_datapoints mismatch: "
            f"manifest={manifest['num_datapoints']} loaded={num_datapoints}"
        )

    loaded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return DatasetCache(
        version=version,
        loaded_at=loaded_at,
        embedding_model=str(manifest["embedding_model"]),
        dimensions=int(manifest["dimensions"]),
        num_datapoints=num_datapoints,
        datapoints_generation=datapoints_generation,
        chunks_generation=chunks_generation,
        manifest_generation=manifest_generation,
        ids=ids,
        embeddings=embeddings,
        chunks_by_id=chunks_by_id,
    )


def _expected_embedding_model() -> str:
    """Return the embedding model expected by the runtime configuration."""
    for key in _EMBEDDING_MODEL_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value.strip()
    return _DEFAULT_EMBEDDING_MODEL


def _parse_manifest(
    data: bytes,
    *,
    manifest_path: str,
    expected_version: str,
) -> Mapping[str, Any]:
    """Parse and validate the manifest JSON structure."""
    try:
        manifest = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Manifest is not valid JSON: {manifest_path}") from exc

    if not isinstance(manifest, Mapping):
        raise RuntimeError(f"Manifest must be a JSON object: {manifest_path}")

    keys = set(manifest.keys())
    if keys != _MANIFEST_REQUIRED_KEYS:
        missing = sorted(_MANIFEST_REQUIRED_KEYS - keys)
        extra = sorted(keys - _MANIFEST_REQUIRED_KEYS)
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"extra {extra}")
        raise RuntimeError(f"Manifest schema mismatch ({'; '.join(detail)}): {manifest_path}")

    version_value = manifest.get("version")
    if not isinstance(version_value, str) or version_value.strip() != expected_version:
        raise RuntimeError(
            "Manifest version mismatch: "
            f"pointer={expected_version} manifest={version_value}"
        )
    return manifest


def _validate_manifest_filenames(manifest: Mapping[str, Any]) -> None:
    """Ensure the manifest references the expected artifact filenames."""
    datapoints_file = manifest.get("datapoints_file")
    chunks_file = manifest.get("chunks_file")
    if datapoints_file != _DATAPOINTS_FILENAME:
        raise RuntimeError(
            "Manifest datapoints_file must be "
            f"'{_DATAPOINTS_FILENAME}', got '{datapoints_file}'"
        )
    if chunks_file != _CHUNKS_FILENAME:
        raise RuntimeError(
            "Manifest chunks_file must be "
            f"'{_CHUNKS_FILENAME}', got '{chunks_file}'"
        )


def _validate_manifest_model(manifest: Mapping[str, Any], expected_model: str) -> None:
    """Ensure the manifest embedding model matches runtime expectations."""
    model_value = manifest.get("embedding_model")
    if not isinstance(model_value, str) or not model_value.strip():
        raise RuntimeError("Manifest embedding_model must be a non-empty string")
    if model_value.strip() != expected_model:
        raise RuntimeError(
            "Embedding model mismatch: "
            f"expected={expected_model} manifest={model_value}"
        )


def _validate_manifest_dimensions(manifest: Mapping[str, Any]) -> None:
    """Ensure the manifest dimensions match configured expectations when set."""
    env_dim = os.getenv("DATAPOINTS_DIMENSIONS")
    if not env_dim:
        return
    try:
        expected_dim = int(env_dim)
    except ValueError as exc:
        raise RuntimeError(f"Invalid DATAPOINTS_DIMENSIONS: {env_dim}") from exc
    if int(manifest.get("dimensions", 0)) != expected_dim:
        raise RuntimeError(
            "Embedding dimensions mismatch: "
            f"expected={expected_dim} manifest={manifest.get('dimensions')}"
        )


def _read_gcs_object(bucket_name: str, object_path: str) -> Tuple[bytes, Optional[str]]:
    """Read a GCS object and return its bytes plus generation."""
    if object_path.startswith("gs://"):
        bucket_name, object_path = _split_gcs_uri(object_path)
    object_name = object_path.lstrip("/")
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("google-cloud-storage is required to load datasets") from exc

    client: Any = storage.Client()
    bucket: Any = client.bucket(bucket_name)
    blob: Any = bucket.blob(object_name)
    data: bytes = blob.download_as_bytes(timeout=settings.request_timeout_seconds)
    generation = getattr(blob, "generation", None)
    generation_value = str(generation) if generation is not None else None
    return data, generation_value


def _split_gcs_uri(uri: str) -> Tuple[str, str]:
    """Split a gs:// URI into bucket and object path."""
    if not uri.startswith("gs://"):
        raise RuntimeError(f"Invalid GCS URI: {uri}")
    path = uri[len("gs://") :]
    bucket, _, object_name = path.partition("/")
    if not bucket or not object_name:
        raise RuntimeError(f"Invalid GCS URI: {uri}")
    return bucket, object_name


def _load_chunks(data: bytes, source: str) -> Dict[str, Dict[str, Any]]:
    """Load chunk records from a gzipped JSONL payload."""
    stream = io.BytesIO(data)
    with gzip.open(stream, "rt", encoding="utf-8") as handle:
        mapping: Dict[str, Dict[str, Any]] = {}
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            chunk_id = str(record.get("id") or "")
            text = record.get("text")
            if not chunk_id or not isinstance(text, str):
                continue
            metadata_obj = record.get("metadata")
            metadata: Dict[str, Any] = {}
            if isinstance(metadata_obj, Mapping):
                metadata = dict(metadata_obj)
            mapping[chunk_id] = {"id": chunk_id, "text": text, "metadata": metadata}
    if not mapping:
        raise RuntimeError(f"No chunk records loaded from {source}")
    return mapping


def _load_datapoints(
    data: bytes,
    source: str,
    *,
    chunks_by_id: Mapping[str, Any],
    manifest_dimensions: int,
) -> Tuple[List[str], List[Sequence[float]]]:
    """Load datapoints from JSONL and validate ids, dimensions, and norms."""
    ids: List[str] = []
    embeddings: List[Sequence[float]] = []
    seen: set[str] = set()
    handle = io.StringIO(data.decode("utf-8"))
    for line in handle:
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        datapoint_id = record.get("id") or record.get("datapointId")
        if not isinstance(datapoint_id, str) or not datapoint_id:
            raise RuntimeError(f"Datapoint missing id field in {source}")
        if datapoint_id in seen:
            raise RuntimeError(f"Duplicate datapoint id '{datapoint_id}' in {source}")
        seen.add(datapoint_id)

        vector = record.get("featureVector")
        if not isinstance(vector, list) or not vector:
            raise RuntimeError(f"Datapoint '{datapoint_id}' missing featureVector in {source}")
        if len(vector) != manifest_dimensions:
            raise RuntimeError(
                "Embedding dimension mismatch: "
                f"id={datapoint_id} expected={manifest_dimensions} got={len(vector)}"
            )

        if datapoint_id not in chunks_by_id:
            raise RuntimeError(
                f"Datapoint id '{datapoint_id}' missing in chunks file {source}"
            )

        float_vector = [float(x) for x in vector]
        _validate_unit_norm(float_vector, datapoint_id)
        ids.append(datapoint_id)
        embeddings.append(array("f", float_vector))

    if not ids:
        raise RuntimeError(f"No datapoints loaded from {source}")
    return ids, embeddings


def _validate_unit_norm(vector: Sequence[float], datapoint_id: str) -> None:
    """Validate that a vector is approximately unit-length."""
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm < _NORM_MIN or norm > _NORM_MAX:
        raise RuntimeError(
            "Datapoint vector norm out of range: "
            f"id={datapoint_id} norm={norm:.4f} expected~1.0"
        )
