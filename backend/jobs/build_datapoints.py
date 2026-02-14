"""Generate normalized datapoints and a dataset manifest from persona chunks."""

from __future__ import annotations

import gzip
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Protocol,
    Sequence,
    cast,
)

try:
    from google import genai  # type: ignore[import-not-found]
    from google.genai import types  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised in environments without SDK
    genai = None  # type: ignore[assignment]

    class _EmbedContentConfigFallback:
        """Fallback EmbedContentConfig used when google-genai is unavailable."""

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class _HttpOptionsFallback:
        """Fallback HttpOptions used when google-genai is unavailable."""

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class _TypesFallback:
        """Fallback namespace matching the small subset of SDK types used here."""

        EmbedContentConfig = _EmbedContentConfigFallback
        HttpOptions = _HttpOptionsFallback

    types = _TypesFallback()  # type: ignore[assignment]

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from jobs.pack_and_push import (
    build_persona_records,
    load_backend_env,
    maybe_set_service_account,
    resolve_existing_path,
)
from api.dataset_schema import SUPPORTED_CHUNK_SCHEMA_VERSION

_DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
_MODEL_DEFAULT_DIMENSIONS: dict[str, int] = {
    "text-embedding-004": 768,
    "gemini-embedding-001": 3072,
}
_MODEL_MAX_OUTPUT_DIMENSIONS: dict[str, int] = {
    "text-embedding-004": 768,
    "gemini-embedding-001": 3072,
}
_GENERIC_MAX_OUTPUT_DIMENSION = 768
_FALLBACK_DEFAULT_DIMENSION = 768

_ENV_PROJECT_ID = "PROJECT_ID"
_ENV_REGION = "REGION"
_ENV_GOOGLE_APPLICATION_CREDENTIALS = "GOOGLE_APPLICATION_CREDENTIALS"
_ENV_DATAPOINTS_FILE = "DATAPOINTS_FILE"
_ENV_DATAPOINTS_GZIP = "DATAPOINTS_GZIP"
_ENV_DATAPOINTS_SCHEMA = "DATAPOINTS_SCHEMA"
_ENV_DATAPOINTS_INPUT = "DATAPOINTS_INPUT"
_ENV_DATAPOINTS_MAX_CHARS = "DATAPOINTS_MAX_CHARS"
_ENV_DATAPOINTS_MODEL = "DATAPOINTS_MODEL"
_ENV_DATAPOINTS_DIMENSIONS = "DATAPOINTS_DIMENSIONS"
_ENV_DATAPOINTS_BATCH_SIZE = "DATAPOINTS_BATCH_SIZE"
_ENV_PRIVATE_DIR = "PRIVATE_DIR"
_ENV_REQ_TIMEOUT_MS = "REQ_TIMEOUT_MS"

_DEFAULT_PRIVATE_DIR_NAME = "private"
_DEFAULT_PERSONA_CHUNKS_FILENAME = "chunks.jsonl"
_DEFAULT_CHUNK_SCHEMA_FILENAME = "chunk.schema.json"
_DEFAULT_DATAPOINTS_GZIP = "0"
_DEFAULT_DATAPOINTS_MAX_CHARS = 2200
_DEFAULT_DATAPOINTS_BATCH_SIZE = 16
_DEFAULT_REQ_TIMEOUT_MS = 20000
_DEFAULT_GCP_PROJECT_ENV_KEYS = (
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "CLOUD_ML_PROJECT_ID",
)
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

_METADATA_PROFILE_KEY = "profile"
_METADATA_DOC_ID_KEY = "doc_id"
_METADATA_TOPICS_KEY = "topics"
_METADATA_TAGS_KEY = "tags"
_METADATA_SECTION_KEY = "section"
_RECORD_TEXT_KEY = "text"

_RESTRICT_NAMESPACE_PROFILE = "profile"
_RESTRICT_NAMESPACE_DOC_ID = "doc_id"
_RESTRICT_NAMESPACE_TOPIC = "topic"
_RESTRICT_NAMESPACE_TAG = "tag"

_DATAPOINT_ID_FIELD = "id"
_DATAPOINT_ID_ALIAS_FIELD = "datapointId"
_DATAPOINT_FEATURE_VECTOR_FIELD = "featureVector"
_DATAPOINT_CROWDING_TAG_FIELD = "crowdingTag"
_DATAPOINT_RESTRICTS_FIELD = "restricts"
_EMBEDDING_OUTPUT_DIMENSION_FIELD = "output_dimensionality"

_DATASET_CHUNKS_FILENAME = "chunks.jsonl.gz"
_DATASET_DATAPOINTS_FILENAME = "datapoints.jsonl"
_DATASET_MANIFEST_FILENAME = "manifest.json"


class _EmbedContentModelsClient(Protocol):
    """Typed surface for the GenAI client's embedding model operations."""

    def embed_content(
        self,
        *,
        model: str,
        contents: Sequence[str],
        config: types.EmbedContentConfig | None = None,
    ) -> object:
        ...


class _GenaiEmbeddingClient(Protocol):
    """Typed surface for the subset of GenAI client APIs used in this job."""

    @property
    def models(self) -> _EmbedContentModelsClient:
        ...


def _env_bool(name: str, default: str = "0") -> bool:
    """Read a boolean-like environment variable.

    Inputs:
    - name: Environment variable name to read.
    - default: String value used when the variable is missing or empty.

    Output:
    - True when the variable contains a known truthy value; otherwise False.

    Edge cases:
    - Missing or blank variables fall back to the provided default.
    - Values are normalized to lowercase before comparison.
    """
    value = os.getenv(name, default)
    return value.lower() in _TRUTHY_ENV_VALUES


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable.

    Inputs:
    - name: Environment variable name to read.
    - default: Integer fallback when the variable is missing or empty.

    Output:
    - Parsed integer value.

    Edge cases:
    - Missing or blank variables return the default.
    - Non-integer values raise a RuntimeError with context.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer for {name}: {raw}") from exc


def _batched(
    items: Sequence[dict[str, object]],
    size: int,
) -> Iterator[Sequence[dict[str, object]]]:
    """Yield fixed-size batches for a sequence of records.

    Inputs:
    - items: Sequence of records to batch.
    - size: Maximum batch size; must be positive.

    Output:
    - Iterator of slices from the input sequence.

    Edge cases:
    - The final batch may contain fewer than `size` items.
    """
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _build_restricts(metadata: Mapping[str, object]) -> list[dict[str, object]]:
    """Build Matching Engine restricts from persona metadata.

    Inputs:
    - metadata: Mapping of persona metadata fields.

    Output:
    - List of restrict dicts suitable for Vertex AI Matching Engine.

    Edge cases:
    - Unknown or empty metadata fields are ignored.
    - Only list-typed topic/tag fields are accepted.
    """
    restricts: list[dict[str, object]] = []

    profile_value = _resolve_profile_for_restricts(metadata)
    if profile_value is not None:
        restricts.append(
            {
                "namespace": _RESTRICT_NAMESPACE_PROFILE,
                "allowTokens": [profile_value],
            }
        )

    doc_id = metadata.get(_METADATA_DOC_ID_KEY)
    if isinstance(doc_id, str) and doc_id:
        restricts.append(
            {"namespace": _RESTRICT_NAMESPACE_DOC_ID, "allowTokens": [doc_id]}
        )

    topics = metadata.get(_METADATA_TOPICS_KEY)
    if isinstance(topics, list) and topics:
        restricts.append(
            {"namespace": _RESTRICT_NAMESPACE_TOPIC, "allowTokens": topics}
        )

    tags = metadata.get(_METADATA_TAGS_KEY)
    if isinstance(tags, list) and tags:
        restricts.append({"namespace": _RESTRICT_NAMESPACE_TAG, "allowTokens": tags})

    return restricts


def _resolve_profile_for_restricts(metadata: Mapping[str, object]) -> str | None:
    """Resolve canonical profile value for datapoint restrict metadata.

    Inputs:
    - metadata: Chunk metadata mapping.

    Output:
    - Lowercase profile string used in the `profile` restrict namespace, or None.

    Edge cases:
    - Returns None when `profile` is missing or blank.
    """
    profile_value = metadata.get(_METADATA_PROFILE_KEY)
    if isinstance(profile_value, str) and profile_value.strip():
        return profile_value.strip().lower()
    return None


def _embedding_values(embedding: object) -> List[float]:
    """Extract embedding vector values from Gen AI embedding responses.

    Inputs:
    - embedding: Gen AI embedding object with one of the expected fields.

    Output:
    - List of float values representing the embedding.

    Edge cases:
    - Raises RuntimeError when no expected values field is present.
    """
    values = getattr(embedding, "values", None)
    if values is None:
        values = getattr(embedding, "embedding", None)
    if values is None:
        values = getattr(embedding, "embedding_values", None)
    if values is None:
        raise RuntimeError("Embedding response missing values field")
    return list(values)


def _extract_embeddings(response: object) -> Sequence[object]:
    """Extract embeddings from a Gen AI SDK response object.

    Inputs:
    - response: SDK response returned by embed_content.

    Output:
    - Sequence of embedding objects.

    Edge cases:
    - Raises RuntimeError when embeddings are missing or empty.
    """
    embeddings = getattr(response, "embeddings", None)
    if not embeddings:
        raise RuntimeError("Embedding response missing embeddings list")
    return cast(Sequence[object], embeddings)


def _l2_normalize(vector: Iterable[float]) -> List[float]:
    """Normalize a vector to unit length.

    Inputs:
    - vector: Iterable of numeric values.

    Output:
    - List of float values scaled to unit norm.

    Edge cases:
    - Raises RuntimeError for zero vectors to avoid invalid normalization.
    """
    values = [float(x) for x in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise RuntimeError("Embedding vector has zero norm; cannot normalize")
    scale = 1.0 / norm
    return [v * scale for v in values]


def _write_datapoints(
    records: Sequence[dict[str, object]],
    embeddings: Sequence[Iterable[float]],
    output_path: Path,
    *,
    gzip_output: bool,
) -> None:
    """Write Matching Engine datapoints in JSON Lines format.

    Inputs:
    - records: Persona records aligned with embeddings.
    - embeddings: Embedding vectors in the same order as records.
    - output_path: Destination path for the JSONL output.
    - gzip_output: Whether to gzip-compress the output file.

    Output:
    - None; writes to disk.

    Edge cases:
    - Writes nothing when `records` is empty.
    - Records with missing metadata fall back to empty metadata.

    Concurrency/atomicity:
    - This function writes sequentially to a single file handle and is not
      atomic across processes.
    """
    handle_fn = gzip.open if gzip_output else open
    mode = "wt"
    with handle_fn(output_path, mode, encoding="utf-8") as handle:
        for record, vector in zip(records, embeddings):
            metadata_value = record.get("metadata")
            metadata_dict: MutableMapping[str, object]
            if metadata_value is None:
                metadata_dict = {}
            elif isinstance(metadata_value, Mapping):
                metadata_mapping = cast(Mapping[str, object], metadata_value)
                metadata_dict = dict(metadata_mapping)
            else:
                metadata_dict = {}

            restricts = _build_restricts(metadata_dict)
            datapoint_id = str(record[_DATAPOINT_ID_FIELD])
            # Vertex AI batch updates require `id`, while the retrieval path still
            # reads `datapointId`; emit both and keep them identical.
            datapoint: dict[str, object] = {
                _DATAPOINT_ID_ALIAS_FIELD: datapoint_id,
                _DATAPOINT_ID_FIELD: datapoint_id,
                _DATAPOINT_FEATURE_VECTOR_FIELD: _l2_normalize(vector),
            }
            section = metadata_dict.get(_METADATA_SECTION_KEY)
            if isinstance(section, str) and section:
                datapoint[_DATAPOINT_CROWDING_TAG_FIELD] = section
            if restricts:
                datapoint[_DATAPOINT_RESTRICTS_FIELD] = restricts
            handle.write(json.dumps(datapoint, ensure_ascii=False))
            handle.write("\n")


def _write_dataset_manifest(
    *,
    output_dir: Path,
    dataset_version: str,
    embedding_model: str,
    dimensions: int,
    num_datapoints: int,
) -> Path:
    """Write the dataset manifest required by the runtime loader."""
    manifest: dict[str, object] = {
        "dataset_version": dataset_version,
        "chunk_schema_version": SUPPORTED_CHUNK_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "datapoints_file": _DATASET_DATAPOINTS_FILENAME,
        "chunks_file": _DATASET_CHUNKS_FILENAME,
        "embedding_model": embedding_model,
        "dimensions": dimensions,
        "num_datapoints": num_datapoints,
    }
    manifest_path = output_dir / _DATASET_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> None:
    """Generate embeddings for persona chunks and write datapoints.

    Inputs:
    - Reads configuration from environment variables and schema/input files.

    Output:
    - Writes a JSONL (optionally gzipped) datapoints file on disk.

    Edge cases:
    - Raises RuntimeError when configuration is missing or invalid.
    - Raises RuntimeError when embedding responses are incomplete.
    """
    backend_root = Path(__file__).resolve().parent.parent
    repo_root = backend_root.parent

    environment_variables = load_backend_env(
        [_ENV_PROJECT_ID, _ENV_REGION],
        optional=[_ENV_GOOGLE_APPLICATION_CREDENTIALS],
    )
    default_schema = backend_root / "schema" / _DEFAULT_CHUNK_SCHEMA_FILENAME
    private_dir = Path(
        os.environ.get(_ENV_PRIVATE_DIR, repo_root / _DEFAULT_PRIVATE_DIR_NAME)
    ).expanduser()
    default_input = private_dir / "persona" / "data" / _DEFAULT_PERSONA_CHUNKS_FILENAME

    datapoints_output_value = os.getenv(_ENV_DATAPOINTS_FILE)
    if not datapoints_output_value:
        raise RuntimeError(
            f"{_ENV_DATAPOINTS_FILE} must be set in the environment "
            "(configure it in backend.env)"
        )
    output_path = Path(datapoints_output_value).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gzip_output = _env_bool(_ENV_DATAPOINTS_GZIP, _DEFAULT_DATAPOINTS_GZIP)
    if gzip_output:
        raise RuntimeError(
            f"{_ENV_DATAPOINTS_GZIP} must be 0; "
            f"dataset datapoints must be {_DATASET_DATAPOINTS_FILENAME}"
        )
    if output_path.name != _DATASET_DATAPOINTS_FILENAME:
        raise RuntimeError(
            f"{_ENV_DATAPOINTS_FILE} must point to "
            f".../{_DATASET_DATAPOINTS_FILENAME} for dataset builds"
        )
    dataset_dir = output_path.parent
    dataset_version = dataset_dir.name
    if not dataset_version:
        raise RuntimeError(
            f"Cannot infer dataset version from {output_path}; "
            "use a versioned folder like datasets/v13/"
        )
    chunks_path = dataset_dir / _DATASET_CHUNKS_FILENAME
    if not chunks_path.is_file():
        raise RuntimeError(
            f"Missing {_DATASET_CHUNKS_FILENAME} in {dataset_dir}; "
            "build chunks before datapoints"
        )

    schema_override = os.getenv(_ENV_DATAPOINTS_SCHEMA)
    input_override = os.getenv(_ENV_DATAPOINTS_INPUT)

    schema_path = resolve_existing_path(
        schema_override or str(default_schema), backend_root
    )
    input_path = resolve_existing_path(
        input_override or str(default_input), repo_root, backend_root
    )

    max_characters = _env_int(
        _ENV_DATAPOINTS_MAX_CHARS, _DEFAULT_DATAPOINTS_MAX_CHARS
    )

    records = build_persona_records(
        schema_path, input_path, max_chars=max_characters
    )
    if not records:
        raise RuntimeError("No persona records found; check the input file")

    project_id = environment_variables[_ENV_PROJECT_ID]
    for env_key in _DEFAULT_GCP_PROJECT_ENV_KEYS:
        os.environ[env_key] = project_id

    maybe_set_service_account(environment_variables)

    region = environment_variables[_ENV_REGION]
    print(f"Using Vertex project '{project_id}' in region '{region}'")
    model_name = (
        os.getenv(_ENV_DATAPOINTS_MODEL, "").strip() or _DEFAULT_EMBEDDING_MODEL
    )
    model_default_dimension = _MODEL_DEFAULT_DIMENSIONS.get(model_name)
    default_dimension = model_default_dimension or _MODEL_DEFAULT_DIMENSIONS.get(
        _DEFAULT_EMBEDDING_MODEL, _FALLBACK_DEFAULT_DIMENSION
    )
    dimensions = _env_int(_ENV_DATAPOINTS_DIMENSIONS, default_dimension)
    if dimensions <= 0:
        raise RuntimeError(
            f"{_ENV_DATAPOINTS_DIMENSIONS} must be a positive integer"
        )
    print(f"Embedding model '{model_name}' @ {dimensions} dims")
    if genai is None:
        raise RuntimeError(
            "google-genai is required to build datapoints. Install backend dependencies "
            "or run this job in an environment with the SDK available."
        )
    http_options = types.HttpOptions(
        timeout=_env_int(_ENV_REQ_TIMEOUT_MS, _DEFAULT_REQ_TIMEOUT_MS)
    )
    client = cast(
        _GenaiEmbeddingClient,
        genai.Client(
            vertexai=True,
            project=project_id,
            location=region,
            http_options=http_options,
        ),
    )
    max_output_dimension = _MODEL_MAX_OUTPUT_DIMENSIONS.get(
        model_name, _GENERIC_MAX_OUTPUT_DIMENSION
    )

    embeddings: list[List[float]] = []
    batch_size = _env_int(_ENV_DATAPOINTS_BATCH_SIZE, _DEFAULT_DATAPOINTS_BATCH_SIZE)
    embedding_output_dimension: int | None = None
    if dimensions == model_default_dimension:
        embedding_output_dimension = None
    elif dimensions <= max_output_dimension:
        embedding_output_dimension = dimensions
    else:
        limit_message = f"<={max_output_dimension}"
        if model_default_dimension:
            limit_message = f"{limit_message} or exactly {model_default_dimension}"
        raise RuntimeError(
            "{env}={dim} is not supported for model '{model}'; "
            "Vertex AI only allows output_dimensionality {limit}. "
            "Either lower {env} or update the model/index configuration.".format(
                env=_ENV_DATAPOINTS_DIMENSIONS,
                dim=dimensions,
                model=model_name,
                limit=limit_message,
            )
        )
    for batch in _batched(records, batch_size):
        texts: list[str] = []
        for record in batch:
            text_value = record.get(_RECORD_TEXT_KEY)
            if not isinstance(text_value, str):
                raise RuntimeError(
                    f"Record {_RECORD_TEXT_KEY} must be a string; "
                    f"received {type(text_value).__name__}"
                )
            texts.append(text_value)

        embed_config = (
            types.EmbedContentConfig(
                output_dimensionality=embedding_output_dimension,
            )
            if embedding_output_dimension is not None
            else None
        )
        response = client.models.embed_content(
            model=model_name,
            contents=texts,
            config=embed_config,
        )
        response_embeddings = _extract_embeddings(response)
        embeddings.extend(_embedding_values(resp) for resp in response_embeddings)

    if len(embeddings) != len(records):
        raise RuntimeError(
            "Embedding response count did not match records; "
            "aborting without writing datapoints"
        )

    _write_datapoints(records, embeddings, output_path, gzip_output=gzip_output)

    total = len(records)
    observed_dimension_count = len(next(iter(embeddings))) if embeddings else 0
    if observed_dimension_count != dimensions:
        raise RuntimeError(
            "Embedding dimension mismatch: "
            f"expected {dimensions}, received {observed_dimension_count}. "
            f"Ensure {_ENV_DATAPOINTS_DIMENSIONS} matches the embedding model output."
        )
    manifest_path = _write_dataset_manifest(
        output_dir=dataset_dir,
        dataset_version=dataset_version,
        embedding_model=model_name,
        dimensions=observed_dimension_count,
        num_datapoints=total,
    )
    print(
        "Wrote "
        f"{total} datapoints ({observed_dimension_count} dims) to {output_path}"
    )
    print(f"Wrote dataset manifest to {manifest_path}")


if __name__ == "__main__":
    main()
