"""Generate Vertex AI Matching Engine datapoints from persona chunks."""

from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Mapping, MutableMapping, Sequence, cast

import vertexai
from vertexai.language_models import (
    TextEmbeddingModel,
)

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

_DEFAULT_PRIVATE_DIR_NAME = "private"
_DEFAULT_PERSONA_CHUNKS_FILENAME = "chunks.jsonl"
_DEFAULT_CHUNK_SCHEMA_FILENAME = "chunk.schema.json"
_DEFAULT_DATAPOINTS_GZIP = "0"
_DEFAULT_DATAPOINTS_MAX_CHARS = 2200
_DEFAULT_DATAPOINTS_BATCH_SIZE = 16
_DEFAULT_GCP_PROJECT_ENV_KEYS = (
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "CLOUD_ML_PROJECT_ID",
)
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

_METADATA_ROLE_KEY = "role"
_METADATA_DOC_ID_KEY = "doc_id"
_METADATA_TOPICS_KEY = "topics"
_METADATA_TAGS_KEY = "tags"
_METADATA_SECTION_KEY = "section"
_RECORD_TEXT_KEY = "text"

_RESTRICT_NAMESPACE_ROLE = "role"
_RESTRICT_NAMESPACE_DOC_ID = "doc_id"
_RESTRICT_NAMESPACE_TOPIC = "topic"
_RESTRICT_NAMESPACE_TAG = "tag"

_DATAPOINT_ID_FIELD = "id"
_DATAPOINT_ID_ALIAS_FIELD = "datapointId"
_DATAPOINT_FEATURE_VECTOR_FIELD = "featureVector"
_DATAPOINT_CROWDING_TAG_FIELD = "crowdingTag"
_DATAPOINT_RESTRICTS_FIELD = "restricts"
_EMBEDDING_OUTPUT_DIMENSION_FIELD = "output_dimensionality"


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

    role = metadata.get(_METADATA_ROLE_KEY)
    if isinstance(role, str) and role:
        restricts.append({"namespace": _RESTRICT_NAMESPACE_ROLE, "allowTokens": [role]})

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


def _embedding_values(embedding: object) -> List[float]:
    """Extract embedding vector values from Vertex AI responses.

    Inputs:
    - embedding: Vertex AI embedding object with one of the expected fields.

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
                _DATAPOINT_FEATURE_VECTOR_FIELD: list(vector),
            }
            section = metadata_dict.get(_METADATA_SECTION_KEY)
            if isinstance(section, str) and section:
                datapoint[_DATAPOINT_CROWDING_TAG_FIELD] = section
            if restricts:
                datapoint[_DATAPOINT_RESTRICTS_FIELD] = restricts
            handle.write(json.dumps(datapoint, ensure_ascii=False))
            handle.write("\n")


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
    if gzip_output and not output_path.suffix.endswith(".gz"):
        output_path = output_path.with_suffix(output_path.suffix + ".gz")

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

    vertexai.init(project=project_id, location=environment_variables[_ENV_REGION])
    print(
        "Using Vertex project "
        f"'{project_id}' in region '{environment_variables[_ENV_REGION]}'"
    )
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
    model = TextEmbeddingModel.from_pretrained(model_name)
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
        texts: list[Any] = []
        for record in batch:
            text_value = record.get(_RECORD_TEXT_KEY)
            if not isinstance(text_value, str):
                raise RuntimeError(
                    f"Record {_RECORD_TEXT_KEY} must be a string; "
                    f"received {type(text_value).__name__}"
                )
            texts.append(text_value)

        if embedding_output_dimension is None:
            responses = model.get_embeddings(texts)
        else:
            responses = model.get_embeddings(
                texts,
                output_dimensionality=embedding_output_dimension,
            )
        embeddings.extend(_embedding_values(resp) for resp in responses)

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
    print(
        "Wrote "
        f"{total} datapoints ({observed_dimension_count} dims) to {output_path}"
    )
    print(
        "Ready for make gcp-index-upsert "
        f"(uses {_ENV_DATAPOINTS_FILE} from backend.env)\n"
    )


if __name__ == "__main__":
    main()
