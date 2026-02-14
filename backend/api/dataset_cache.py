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
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, cast

from .dataset_schema import SUPPORTED_CHUNK_SCHEMA_VERSION
from .settings import settings

_DATASET_FOLDER = "datasets"
_DATASET_POINTER_PATH = f"{_DATASET_FOLDER}/current.json"
_DATASET_POINTER_DATASET_VERSION_KEY = "dataset_version"
_MANIFEST_REQUIRED_KEYS = {
    "dataset_version",
    "chunk_schema_version",
    "created_at",
    "datapoints_file",
    "chunks_file",
    "embedding_model",
    "dimensions",
    "num_datapoints",
}

_EMBEDDING_MODEL_ENV_KEYS = ("EMBEDDING_MODEL", "DATAPOINTS_MODEL")
_DATAPOINTS_DIMENSIONS_ENV_KEY = "DATAPOINTS_DIMENSIONS"
_DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
_NORM_TOLERANCE = 0.15
_NORM_MIN = 1.0 - _NORM_TOLERANCE
_NORM_MAX = 1.0 + _NORM_TOLERANCE

_CHUNKS_FILENAME = "chunks.jsonl.gz"
_DATAPOINTS_FILENAME = "datapoints.jsonl"
_MANIFEST_FILENAME = "manifest.json"

_DATAPOINT_ID_KEY = "id"
_DATAPOINT_ALT_ID_KEY = "datapointId"
_DATAPOINT_VECTOR_KEY = "featureVector"
_CHUNK_TEXT_KEY = "text"
_CHUNK_METADATA_KEY = "metadata"

_GCS_URI_PREFIX = "gs://"
_FILE_URI_PREFIX = "file:"
_UTC_Z_SUFFIX = "Z"
_UTF8_ENCODING = "utf-8"

_GCS_CLIENT_FACTORY_TYPE = Callable[[], Any]

_cache_lock = threading.Lock()
_dataset_cache: Optional["DatasetCache"] = None


class ChunkSchemaVersionError(RuntimeError):
    """Error raised when a dataset manifest chunk schema version is unsupported.

    Attributes:
        expected_chunk_schema_version: Integer chunk schema version supported by this codebase.
        found_chunk_schema_version: Integer chunk schema version found in the manifest, when present.

    Edge cases:
        `found_chunk_schema_version` is None when the manifest omitted
        chunk_schema_version.
    """

    def __init__(
        self,
        message: str,
        *,
        expected_chunk_schema_version: int,
        found_chunk_schema_version: Optional[int],
    ) -> None:
        super().__init__(message)
        self.expected_chunk_schema_version = expected_chunk_schema_version
        self.found_chunk_schema_version = found_chunk_schema_version


@dataclass(frozen=True)
class DatasetCache:
    """In-memory dataset snapshot built from a versioned dataset location.

    Attributes:
        dataset_version: Dataset version resolved from the pointer file.
        chunk_schema_version: Dataset manifest chunk schema version.
        loaded_at: UTC ISO-8601 timestamp when the cache was built.
        embedding_model: Embedding model name from the manifest.
        dimensions: Embedding dimensionality from the manifest.
        num_datapoints: Total number of datapoints loaded.
        datapoints_generation: Optional GCS generation for the datapoints blob.
        chunks_generation: Optional GCS generation for the chunks blob.
        manifest_generation: Optional GCS generation for the manifest blob.
        ids: Datapoint ids aligned with embeddings by index.
        embeddings: Embedding vectors aligned with ids by index.
        chunks_by_id: Chunk payloads keyed by datapoint id.
    """

    dataset_version: str
    chunk_schema_version: int
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
    """Dataset pointer information resolved from the pointer file.

    Attributes:
        dataset_version: Dataset version string from the pointer.
        generation: Optional GCS generation for the pointer blob.
    """

    dataset_version: str
    generation: Optional[str]


def get_cache_snapshot() -> Optional[DatasetCache]:
    """Return the current dataset cache without forcing a reload.

    Returns:
        The current DatasetCache instance, or None if nothing has been loaded yet.

    Concurrency:
        Reads the global cache pointer under a lock to avoid torn reads.
    """
    with _cache_lock:
        return _dataset_cache


def get_supported_chunk_schema_version() -> int:
    """Return the chunk schema version supported by this runtime.

    Returns:
        Integer chunk schema version expected in dataset manifests.

    Concurrency:
        Pure constant lookup; no shared mutable state.
    """
    return SUPPORTED_CHUNK_SCHEMA_VERSION


def get_or_load_cache() -> DatasetCache:
    """Return the dataset cache, loading it from the pointer if needed.

    Returns:
        The existing DatasetCache if present, otherwise a freshly loaded cache.

    Edge cases:
        Raises if the pointer or dataset artifacts are missing or invalid.
    """
    cached = get_cache_snapshot()
    if cached is not None:
        return cached
    return reload_cache()


def reload_cache() -> DatasetCache:
    """Reload the dataset cache from the pointer file and swap it atomically.

    Returns:
        The newly loaded DatasetCache instance.

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
    """Load the dataset pointer JSON and return version plus generation.

    Returns:
        PointerInfo with the version from the pointer JSON and optional
        generation metadata.

    Raises:
        RuntimeError: If the pointer file is missing, invalid JSON, or missing
            the expected version key.
    """
    pointer_path = _DATASET_POINTER_PATH
    data, generation = _read_dataset_object(_dataset_root_uri(), pointer_path)
    try:
        payload = json.loads(data.decode(_UTF8_ENCODING))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Pointer file is not valid JSON: {pointer_path}") from exc

    dataset_version = payload.get(_DATASET_POINTER_DATASET_VERSION_KEY)
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise RuntimeError(
            "Pointer file missing "
            f"'{_DATASET_POINTER_DATASET_VERSION_KEY}': {pointer_path}"
        )
    return PointerInfo(dataset_version=dataset_version.strip(), generation=generation)


def _load_cache_from_pointer() -> DatasetCache:
    """Resolve the pointer to a dataset version and load its artifacts.

    Returns:
        A DatasetCache containing ids, embeddings, and chunk metadata for the
        resolved dataset version.

    Raises:
        RuntimeError: If any dataset artifact is missing, corrupt, or fails
            validation against the manifest.
    """
    pointer_info = get_pointer_info()
    dataset_version = pointer_info.dataset_version

    version_prefix = _DATASET_FOLDER
    base_prefix = f"{version_prefix}/{dataset_version}/"

    manifest_path = f"{base_prefix}{_MANIFEST_FILENAME}"
    datapoints_path = f"{base_prefix}{_DATAPOINTS_FILENAME}"
    chunks_path = f"{base_prefix}{_CHUNKS_FILENAME}"

    manifest_data, manifest_generation = _read_dataset_object(
        _dataset_root_uri(),
        manifest_path,
    )
    manifest = _parse_manifest(
        manifest_data,
        manifest_path=manifest_path,
        expected_dataset_version=dataset_version,
    )

    _validate_manifest_filenames(manifest)
    expected_model = _expected_embedding_model()
    _validate_manifest_model(manifest, expected_model)
    _validate_manifest_dimensions(manifest)

    chunks_data, chunks_generation = _read_dataset_object(_dataset_root_uri(), chunks_path)
    chunks_by_id = _load_chunks(chunks_data, chunks_path)

    datapoints_data, datapoints_generation = _read_dataset_object(
        _dataset_root_uri(),
        datapoints_path,
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

    loaded_at = _utc_now_isoformat()
    return DatasetCache(
        dataset_version=dataset_version,
        chunk_schema_version=int(manifest["chunk_schema_version"]),
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
    """Return the embedding model expected by the runtime configuration.

    Returns:
        The first non-empty model found in the configured env vars, otherwise
        the default embedding model name.
    """
    for key in _EMBEDDING_MODEL_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value.strip()
    return _DEFAULT_EMBEDDING_MODEL


def _parse_manifest(
    data: bytes,
    *,
    manifest_path: str,
    expected_dataset_version: str,
) -> Dict[str, Any]:
    """Parse and validate the manifest JSON structure.

    Args:
        data: Raw manifest bytes.
        manifest_path: Path used for error context.
        expected_dataset_version: Dataset version string expected from the pointer.

    Returns:
        A dict with string keys matching the manifest schema.

    Raises:
        RuntimeError: If the JSON is invalid, not an object, missing required
            keys, or has a version mismatch.
    """
    try:
        loaded: Any = json.loads(data.decode(_UTF8_ENCODING))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Manifest is not valid JSON: {manifest_path}") from exc

    if not isinstance(loaded, Mapping):
        raise RuntimeError(f"Manifest must be a JSON object: {manifest_path}")
    manifest = {
        str(key): value
        for key, value in cast(Mapping[str, Any], loaded).items()
    }

    keys = set(manifest.keys())
    if keys != _MANIFEST_REQUIRED_KEYS:
        missing = sorted(_MANIFEST_REQUIRED_KEYS - keys)
        extra = sorted(keys - _MANIFEST_REQUIRED_KEYS)
        if "chunk_schema_version" in missing:
            raise ChunkSchemaVersionError(
                "Manifest chunk_schema_version is missing; chunk schema is unsupported",
                expected_chunk_schema_version=SUPPORTED_CHUNK_SCHEMA_VERSION,
                found_chunk_schema_version=None,
            )
        detail: List[str] = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"extra {extra}")
        raise RuntimeError(f"Manifest schema mismatch ({'; '.join(detail)}): {manifest_path}")

    chunk_schema_version_value = manifest.get("chunk_schema_version")
    if not isinstance(chunk_schema_version_value, int):
        raise ChunkSchemaVersionError(
            "Manifest chunk_schema_version must be an integer",
            expected_chunk_schema_version=SUPPORTED_CHUNK_SCHEMA_VERSION,
            found_chunk_schema_version=None,
        )
    if chunk_schema_version_value != SUPPORTED_CHUNK_SCHEMA_VERSION:
        raise ChunkSchemaVersionError(
            "Unsupported chunk schema version: "
            f"supported={SUPPORTED_CHUNK_SCHEMA_VERSION} "
            f"manifest={chunk_schema_version_value}",
            expected_chunk_schema_version=SUPPORTED_CHUNK_SCHEMA_VERSION,
            found_chunk_schema_version=chunk_schema_version_value,
        )

    dataset_version_value = manifest.get("dataset_version")
    if (
        not isinstance(dataset_version_value, str)
        or dataset_version_value.strip() != expected_dataset_version
    ):
        raise RuntimeError(
            "Manifest dataset_version mismatch: "
            f"pointer={expected_dataset_version} manifest={dataset_version_value}"
        )
    return manifest


def _validate_manifest_filenames(manifest: Mapping[str, Any]) -> None:
    """Ensure the manifest references the expected artifact filenames.

    Args:
        manifest: Parsed manifest mapping with string keys.

    Raises:
        RuntimeError: If any artifact filename differs from expected values.
    """
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
    """Ensure the manifest embedding model matches runtime expectations.

    Args:
        manifest: Parsed manifest mapping with string keys.
        expected_model: The embedding model name expected at runtime.

    Raises:
        RuntimeError: If the manifest model is missing, empty, or mismatched.
    """
    model_value = manifest.get("embedding_model")
    if not isinstance(model_value, str) or not model_value.strip():
        raise RuntimeError(
            "Manifest embedding_model must be a non-empty string"
        )
    if model_value.strip() != expected_model:
        raise RuntimeError(
            "Embedding model mismatch: "
            f"expected={expected_model} manifest={model_value}"
        )


def _validate_manifest_dimensions(manifest: Mapping[str, Any]) -> None:
    """Ensure the manifest dimensions match configured expectations when set.

    Args:
        manifest: Parsed manifest mapping with string keys.

    Raises:
        RuntimeError: If the env var is not an integer or if dimensions mismatch.
    """
    env_dim = os.getenv(_DATAPOINTS_DIMENSIONS_ENV_KEY)
    if not env_dim:
        return
    try:
        expected_dim = int(env_dim)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid {_DATAPOINTS_DIMENSIONS_ENV_KEY}: {env_dim}"
        ) from exc
    if int(manifest.get("dimensions", 0)) != expected_dim:
        raise RuntimeError(
            "Embedding dimensions mismatch: "
            f"expected={expected_dim} manifest={manifest.get('dimensions')}"
        )


def _dataset_root_uri() -> str:
    """Return the dataset root URI for reads (GCS bucket by default).

    Returns:
        The configured dataset root URI or a default GCS URI built from the
        settings bucket name.
    """
    uri = (settings.DATASET_URI or "").strip()
    if uri:
        return uri
    return f"{_GCS_URI_PREFIX}{settings.BUCKET_NAME}"


def _is_local_dataset_root(uri: str) -> bool:
    """Detect local dataset roots without adding new flags.

    Args:
        uri: Dataset root URI string.

    Returns:
        True for local filesystem roots; False for GCS-style URIs.
    """
    return uri.startswith(("/", "./", "../", "~", _FILE_URI_PREFIX))


def _read_dataset_object(
    root_uri: str,
    object_path: str,
    *,
    gcs_client_factory: Optional[_GCS_CLIENT_FACTORY_TYPE] = None,
) -> Tuple[bytes, Optional[str]]:
    """Read a dataset object from local disk or GCS based on the root URI.

    Args:
        root_uri: Dataset root location (local path or GCS URI).
        object_path: Relative path to the dataset artifact under the root.
        gcs_client_factory: Optional factory for creating a GCS client.

    Returns:
        A tuple of raw bytes and optional GCS generation.

    Raises:
        RuntimeError: If the object cannot be read or the URI is invalid.
    """
    root_uri = root_uri.strip()
    if _is_local_dataset_root(root_uri):
        return _read_local_object(root_uri, object_path)

    if root_uri.startswith(_GCS_URI_PREFIX):
        normalized_root = root_uri
    else:
        normalized_root = f"{_GCS_URI_PREFIX}{root_uri}"
    uri = f"{normalized_root.strip().rstrip('/')}/{object_path.lstrip('/')}"
    return _read_gcs_object("", uri, gcs_client_factory=gcs_client_factory)


def _read_local_object(root_uri: str, object_path: str) -> Tuple[bytes, Optional[str]]:
    """Read a dataset object from the local filesystem.

    Args:
        root_uri: Local dataset root (path or file URI).
        object_path: Relative dataset artifact path.

    Returns:
        A tuple of raw bytes and None (no generation for local files).

    Raises:
        RuntimeError: If the file does not exist.
    """
    local_root = root_uri
    if local_root.startswith(_FILE_URI_PREFIX):
        local_root = local_root[len(_FILE_URI_PREFIX) :]
        if local_root.startswith("//"):
            local_root = local_root[2:]
    root_path = Path(local_root).expanduser()
    file_path = root_path / object_path.lstrip("/")
    try:
        return file_path.read_bytes(), None
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing dataset file: {file_path}") from exc


def _read_gcs_object(
    bucket_name: str,
    object_path: str,
    *,
    gcs_client_factory: Optional[_GCS_CLIENT_FACTORY_TYPE] = None,
) -> Tuple[bytes, Optional[str]]:
    """Read a GCS object and return its bytes plus generation.

    Args:
        bucket_name: GCS bucket name, or empty when object_path is a full URI.
        object_path: Object path within the bucket or a gs:// URI.
        gcs_client_factory: Optional factory for creating a GCS client.

    Returns:
        A tuple of raw bytes and optional GCS generation string.

    Raises:
        RuntimeError: If the storage client is missing or the URI is invalid.
    """
    if object_path.startswith(_GCS_URI_PREFIX):
        bucket_name, object_path = _split_gcs_uri(object_path)
    object_name = object_path.lstrip("/")
    client: Any = _create_gcs_client(gcs_client_factory)
    bucket: Any = client.bucket(bucket_name)
    blob: Any = bucket.blob(object_name)
    data: bytes = blob.download_as_bytes(timeout=settings.request_timeout_seconds)
    generation = getattr(blob, "generation", None)
    generation_value = str(generation) if generation is not None else None
    return data, generation_value


def _split_gcs_uri(uri: str) -> Tuple[str, str]:
    """Split a gs:// URI into bucket and object path.

    Args:
        uri: GCS URI string.

    Returns:
        Tuple of (bucket_name, object_path).

    Raises:
        RuntimeError: If the URI is malformed or missing a bucket/object.
    """
    if not uri.startswith(_GCS_URI_PREFIX):
        raise RuntimeError(f"Invalid GCS URI: {uri}")
    path = uri[len(_GCS_URI_PREFIX) :]
    bucket, _, object_name = path.partition("/")
    if not bucket or not object_name:
        raise RuntimeError(f"Invalid GCS URI: {uri}")
    return bucket, object_name


def _create_gcs_client(
    gcs_client_factory: Optional[_GCS_CLIENT_FACTORY_TYPE],
) -> Any:
    """Create a GCS client via dependency injection or default import.

    Args:
        gcs_client_factory: Optional factory that returns a GCS client.

    Returns:
        A storage client compatible with google.cloud.storage.Client.

    Raises:
        RuntimeError: If google-cloud-storage is missing and no factory is given.
    """
    if gcs_client_factory is not None:
        return gcs_client_factory()
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("google-cloud-storage is required to load datasets") from exc
    return storage.Client()


def _utc_now_isoformat(
    *,
    clock: Optional[Callable[[], datetime]] = None,
) -> str:
    """Return the current UTC time as an ISO-8601 string.

    Args:
        clock: Optional callable returning a timezone-aware datetime.

    Returns:
        ISO-8601 string with a trailing Z suffix.
    """
    now = clock() if clock is not None else datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", _UTC_Z_SUFFIX)


def _load_chunks(data: bytes, source: str) -> Dict[str, Dict[str, Any]]:
    """Load chunk records from a gzipped JSONL payload.

    Args:
        data: Raw gzipped JSONL bytes.
        source: Source path used for error context.

    Returns:
        Mapping of chunk id to chunk payload (id, text, metadata).

    Raises:
        RuntimeError: If no valid chunks are loaded from the payload.
    """
    stream = io.BytesIO(data)
    with gzip.open(stream, "rt", encoding=_UTF8_ENCODING) as handle:
        chunks_by_id: Dict[str, Dict[str, Any]] = {}
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record: Dict[str, Any] = json.loads(line)
            chunk_id = str(record.get(_DATAPOINT_ID_KEY) or "")
            text = record.get(_CHUNK_TEXT_KEY)
            if not chunk_id or not isinstance(text, str):
                continue
            metadata_obj = record.get(_CHUNK_METADATA_KEY)
            metadata: Dict[str, Any] = {}
            if isinstance(metadata_obj, Mapping):
                metadata = dict(cast(Mapping[str, Any], metadata_obj))
            chunks_by_id[chunk_id] = {
                _DATAPOINT_ID_KEY: chunk_id,
                _CHUNK_TEXT_KEY: text,
                _CHUNK_METADATA_KEY: metadata,
            }
    if not chunks_by_id:
        raise RuntimeError(f"No chunk records loaded from {source}")
    return chunks_by_id


def _load_datapoints(
    data: bytes,
    source: str,
    *,
    chunks_by_id: Mapping[str, Any],
    manifest_dimensions: int,
) -> Tuple[List[str], List[Sequence[float]]]:
    """Load datapoints from JSONL and validate ids, dimensions, and norms.

    Args:
        data: Raw JSONL bytes.
        source: Source path used for error context.
        chunks_by_id: Mapping of chunk ids to chunk payloads.
        manifest_dimensions: Expected embedding dimensionality from the manifest.

    Returns:
        Tuple of (datapoint_ids, embeddings) aligned by index.

    Raises:
        RuntimeError: If datapoints are missing ids, missing vectors, have
            duplicate ids, mismatched dimensions, missing chunk entries, or
            invalid unit norms.
    """
    datapoint_ids: List[str] = []
    embeddings: List[Sequence[float]] = []
    seen_datapoint_ids: set[str] = set()
    decoded_payload = data.decode(_UTF8_ENCODING)
    handle = io.StringIO(decoded_payload)
    for line in handle:
        line = line.strip()
        if not line:
            continue
        record: Dict[str, Any] = json.loads(line)
        datapoint_id = record.get(_DATAPOINT_ID_KEY) or record.get(_DATAPOINT_ALT_ID_KEY)
        if not isinstance(datapoint_id, str) or not datapoint_id:
            raise RuntimeError(f"Datapoint missing id field in {source}")
        if datapoint_id in seen_datapoint_ids:
            raise RuntimeError(f"Duplicate datapoint id '{datapoint_id}' in {source}")
        seen_datapoint_ids.add(datapoint_id)

        vector_obj = record.get(_DATAPOINT_VECTOR_KEY)
        if not isinstance(vector_obj, list) or not vector_obj:
            raise RuntimeError(
                f"Datapoint '{datapoint_id}' missing {_DATAPOINT_VECTOR_KEY} in {source}"
            )
        vector_values = cast(List[Any], vector_obj)
        if len(vector_values) != manifest_dimensions:
            raise RuntimeError(
                "Embedding dimension mismatch: "
                f"id={datapoint_id} expected={manifest_dimensions} got={len(vector_values)}"
            )

        if datapoint_id not in chunks_by_id:
            raise RuntimeError(
                f"Datapoint id '{datapoint_id}' missing in chunks file {source}"
            )

        float_vector = [float(value) for value in vector_values]
        _validate_unit_norm(float_vector, datapoint_id)
        datapoint_ids.append(datapoint_id)
        embeddings.append(array("f", float_vector))

    if not datapoint_ids:
        raise RuntimeError(f"No datapoints loaded from {source}")
    return datapoint_ids, embeddings


def _validate_unit_norm(vector: Sequence[float], datapoint_id: str) -> None:
    """Validate that a vector is approximately unit-length.

    Args:
        vector: Embedding vector values.
        datapoint_id: Datapoint id used for error context.

    Raises:
        RuntimeError: If the vector norm falls outside the allowed tolerance.
    """
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm < _NORM_MIN or norm > _NORM_MAX:
        raise RuntimeError(
            "Datapoint vector norm out of range: "
            f"id={datapoint_id} norm={norm:.4f} expected~1.0"
        )
