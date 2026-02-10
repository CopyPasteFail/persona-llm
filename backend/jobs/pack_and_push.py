"""Validate persona chunk data, bundle it into a gzipped JSONL file, and upload it to GCS."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping, Protocol, runtime_checkable, cast

from dotenv import dotenv_values
from google.cloud import storage
from jsonschema import Draft202012Validator

DEFAULT_MAX_CHARS = 2200
READ_CHUNK_SIZE_BYTES = 1024 * 1024
SHA1_HEX_LENGTH = 12
OUTPUT_FILENAME_TEMPLATE = "chunks-{digest}.jsonl.gz"


def resolve_existing_path(path_value: str, *roots: Path) -> Path:
    """Resolve a file path against several candidate roots.

    Args:
        path_value: File path string to resolve.
        roots: Additional roots to try for relative paths.

    Returns:
        The first existing path discovered.

    Raises:
        FileNotFoundError: When no candidate path exists.
    """
    candidate = Path(path_value).expanduser()

    search_paths: list[Path] = []
    if candidate.is_absolute():
        search_paths.append(candidate)
    else:
        search_paths.append((Path.cwd() / candidate).resolve())
        for root in roots:
            search_paths.append((root / candidate).resolve())
            if candidate.parts and candidate.parts[0] == root.name:
                search_paths.append((root / Path(*candidate.parts[1:])).resolve())

    for path in search_paths:
        if path.exists():
            return path

    raise FileNotFoundError(f"File not found: {candidate}")


def _expand_env_value(value: str) -> str:
    """Expand env-style tokens like $HOME and ~.

    Args:
        value: Raw string containing environment variables or user home.

    Returns:
        Expanded string with environment variables and user home resolved.
    """
    return os.path.expanduser(os.path.expandvars(value))


def load_backend_env(
    keys: list[str], *, optional: Iterable[str] | None = None
) -> dict[str, str]:
    """Load selected backend secrets from the private directory.

    Args:
        keys: Required environment keys to load.
        optional: Optional environment keys to load if present.

    Returns:
        Mapping of environment keys to their resolved values.

    Raises:
        RuntimeError: If PRIVATE_DIR or required keys are missing.
    """
    private_dir = os.getenv("PRIVATE_DIR")
    if not private_dir:
        raise RuntimeError("PRIVATE_DIR is not set. It must point to the private folder.")

    secrets_dir = Path(private_dir).expanduser().resolve() / "secrets"
    env_path = secrets_dir / "backend.env"
    common_env_path = secrets_dir / "common.env"

    if not env_path.exists():
        raise RuntimeError(f"Missing secrets file: {env_path}")

    env_values: dict[str, str] = {}
    if common_env_path.exists():
        raw_common: Mapping[str, str | None] = cast(
            Mapping[str, str | None], dotenv_values(common_env_path)
        )
        env_values.update({k: _expand_env_value(v) for k, v in raw_common.items() if v})

    raw_env: Mapping[str, str | None] = cast(Mapping[str, str | None], dotenv_values(env_path))
    env_values.update({k: _expand_env_value(v) for k, v in raw_env.items() if v})
    selected: dict[str, str] = {}
    for key in keys:
        value = env_values.get(key) or os.getenv(key)
        if not value:
            raise RuntimeError(f"Missing required env var: {key}")
        selected[key] = _expand_env_value(value)
    if optional:
        for key in optional:
            value = env_values.get(key) or os.getenv(key)
            if value:
                selected[key] = _expand_env_value(value)
    return selected


def maybe_set_service_account(env: Mapping[str, str]) -> None:
    """Apply GOOGLE_APPLICATION_CREDENTIALS from env mapping if present.

    Args:
        env: Environment mapping containing optional credentials path.

    Returns:
        None. Sets an environment variable as a side effect.
    """
    credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        return

    cred_file = Path(credentials_path)
    if cred_file.is_file():
        print(f"Using service account credentials from {cred_file}")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_file)
    else:
        print(
            f"GOOGLE_APPLICATION_CREDENTIALS points to missing file {cred_file}; "
            "falling back to Application Default Credentials"
        )


@runtime_checkable
class _StorageBlob(Protocol):
    def upload_from_filename(self, filename: str, *, timeout: int | None = None) -> None: ...


@runtime_checkable
class _StorageBucket(Protocol):
    def blob(self, object_name: str) -> _StorageBlob: ...


@runtime_checkable
class _StorageClient(Protocol):
    def bucket(self, bucket_name: str) -> _StorageBucket: ...


class _JsonSchemaValidator(Protocol):
    def validate(self, instance: object) -> None: ...


def upload_to_bucket(
    file_path: Path,
    bucket_name: str,
    object_name: str,
    storage_client: _StorageClient | None = None,
) -> str:
    """Upload the artifact to Cloud Storage and return its URI.

    Args:
        file_path: Local file path to upload.
        bucket_name: Target bucket name.
        object_name: Destination object name within the bucket.
        storage_client: Storage client used to access the bucket.
            Defaults to a new client when not provided.

    Returns:
        GCS URI for the uploaded object.
    """
    resolved_storage_client = storage_client or cast(_StorageClient, storage.Client())
    bucket = resolved_storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(file_path))
    return f"gs://{bucket_name}/{object_name}"


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Break text into <= max_chars segments, preferring sentence boundaries.

    Args:
        text: Input text to split.
        max_chars: Maximum length for each returned fragment.

    Returns:
        List of fragments in original order.

    Edge cases:
        Long single sentences may exceed max_chars if no boundary exists.
    """
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    fragments: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            fragments.append(current)
        current = sentence
    if current:
        fragments.append(current)
    return fragments


def deterministic_id(text: str) -> str:
    """Generate a stable SHA-1 hex fragment derived from `text`.

    Args:
        text: Input text to hash.

    Returns:
        A deterministic hex fragment suitable for compact identifiers.
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:SHA1_HEX_LENGTH]


def _load_chunks(input_path: Path, schema: Mapping[str, object]) -> Iterable[dict[str, object]]:
    """Yield validated JSONL chunks from the input file.

    Args:
        input_path: Path to the JSONL input file.
        schema: JSON schema used for validation.

    Yields:
        Parsed chunk dictionaries in file order.

    Raises:
        jsonschema.ValidationError: If any chunk violates the schema.
        json.JSONDecodeError: If any line is invalid JSON.
    """
    validator = cast(_JsonSchemaValidator, Draft202012Validator(schema))
    with open(input_path, "r", encoding="utf-8") as input_handle:
        for line_text in input_handle:
            if not line_text.strip():
                continue
            chunk = json.loads(line_text)
            validator.validate(chunk)
            yield cast(dict[str, object], chunk)


def _build_metadata(
    chunk: Mapping[str, object],
    index: int,
    total: int,
) -> MutableMapping[str, object]:
    """Build metadata for a chunk fragment.

    Args:
        chunk: Original chunk dictionary.
        index: Zero-based fragment index.
        total: Total number of fragments for this chunk.

    Returns:
        Metadata mapping containing selected fields and fragment info.
    """
    metadata: MutableMapping[str, object] = {}
    for key in (
        "doc_id",
        "chunk_id",
        "position",
        "role",
        "section",
        "start_year",
        "end_year",
        "lang",
        "updated_at",
        "source_uri",
    ):
        value = chunk.get(key)
        if value is not None:
            metadata[key] = value

    topics = _coerce_non_empty_list(chunk.get("topics"))
    if topics is not None:
        metadata["topics"] = topics
    tags = _coerce_non_empty_list(chunk.get("tags"))
    if tags is not None:
        metadata["tags"] = tags
    permissions = _coerce_non_empty_list(chunk.get("permissions"))
    if permissions is not None:
        metadata["permissions"] = permissions

    extras = chunk.get("extras")
    if isinstance(extras, dict) and extras:
        extras_map = cast(Mapping[str, object], extras)
        metadata["extras"] = dict(extras_map)

    metadata["fragment_index"] = index
    metadata["fragment_count"] = total
    return metadata


def _coerce_non_empty_list(value: object) -> list[object] | None:
    """Return a non-empty list when the value is a list; otherwise None.

    Args:
        value: Raw value to check and coerce.

    Returns:
        List copy if the value is a non-empty list, otherwise None.
    """
    if isinstance(value, list) and value:
        return list(cast(list[object], value))
    return None


def build_persona_records(
    schema_path: Path,
    input_path: Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict[str, object]]:
    """Load JSONL chunks, split long entries, and enrich metadata.

    Args:
        schema_path: JSON schema file path for validation.
        input_path: JSONL input file path.
        max_chars: Maximum character length for each fragment.

    Returns:
        List of persona records ready for serialization.
    """

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    records: list[dict[str, object]] = []
    for chunk in _load_chunks(input_path, schema):
        text = cast(str, chunk["text"])
        fragments = split_sentences(text, max_chars=max_chars)
        base_id = cast(str, chunk.get("chunk_id") or chunk.get("id") or deterministic_id(text))
        for fragment_index, fragment in enumerate(fragments):
            fragment_id = (
                base_id
                if len(fragments) == 1
                else f"{base_id}:{fragment_index + 1:02d}"
            )
            metadata = _build_metadata(chunk, fragment_index, len(fragments))
            records.append({"id": fragment_id, "text": fragment, "metadata": metadata})
    return records


def _serialize_records(records: Iterable[dict[str, object]]) -> tuple[bytes, str]:
    """Serialize records into JSONL bytes and a deterministic filename.

    Args:
        records: Iterable of persona records.

    Returns:
        Tuple of (payload bytes, output filename).
    """
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records).encode(
        "utf-8"
    )
    digest = hashlib.sha1(content).hexdigest()[:SHA1_HEX_LENGTH]
    return content, OUTPUT_FILENAME_TEMPLATE.format(digest=digest)


def _sha256_digest(path: Path) -> str:
    """Return the SHA-256 hex digest for the given file.

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest string.
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for bytes_chunk in iter(lambda: file_handle.read(READ_CHUNK_SIZE_BYTES), b""):
            hasher.update(bytes_chunk)
    return hasher.hexdigest()


def _write_manifest(
    *,
    artifact_path: Path,
    artifact_uri: str,
    schema_path: Path,
    input_path: Path,
    record_count: int,
) -> Path:
    """Emit a JSON manifest describing the generated chunk artifact.

    Args:
        artifact_path: Path to the generated artifact on disk.
        artifact_uri: URI for the uploaded artifact.
        schema_path: Schema path used to validate inputs.
        input_path: Path to the JSONL input file.
        record_count: Number of records serialized.

    Returns:
        Path to the manifest file written on disk.
    """
    checksum = _sha256_digest(artifact_path)
    manifest: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "artifact": {
            "name": artifact_path.name,
            "uri": artifact_uri,
            "size_bytes": artifact_path.stat().st_size,
            "checksum": {
                "algorithm": "sha256",
                "value": checksum,
            },
        },
        "records": record_count,
        "inputs": {
            "schema": str(schema_path.resolve()),
            "chunks": str(input_path.resolve()),
        },
    }
    manifest_name = f"{artifact_path.name}.manifest.json"
    manifest_path = artifact_path.with_name(manifest_name)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> None:
    """Validate persona chunks, bundle them, and push the archive to GCS.

    Inputs:
        CLI flags for schema and input paths.

    Outputs:
        Uploads a gzipped JSONL artifact and a JSON manifest to GCS.

    Edge cases:
        Raises if required env vars or files are missing or invalid.
    """

    backend_root = Path(__file__).resolve().parent.parent
    repo_root = backend_root.parent
    env = load_backend_env(["BUCKET_NAME"], optional=["GOOGLE_APPLICATION_CREDENTIALS"])

    default_schema = backend_root / "schema" / "chunk.schema.json"
    default_input = repo_root / "private" / "persona" / "data" / "chunks.jsonl"

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default=str(default_schema))
    parser.add_argument("--input", default=str(default_input))
    parsed_args = parser.parse_args()

    schema_path = resolve_existing_path(parsed_args.schema, backend_root)
    input_path = resolve_existing_path(parsed_args.input, repo_root, backend_root)

    maybe_set_service_account(env)

    records = build_persona_records(schema_path, input_path)
    payload, filename = _serialize_records(records)

    out_path = Path(filename)
    with gzip.open(out_path, "wb") as gzip_handle:
        gzip_handle.write(payload)

    bucket = env["BUCKET_NAME"]
    storage_client = cast(_StorageClient, storage.Client())
    uri = upload_to_bucket(out_path, bucket, filename, storage_client=storage_client)
    print(f"Uploaded persona chunks to {uri}")
    print(f"Artifact name: {filename} (set this as CHUNKS_PATH in your backend env)")

    manifest_path = _write_manifest(
        artifact_path=out_path,
        artifact_uri=uri,
        schema_path=schema_path,
        input_path=input_path,
        record_count=len(records),
    )
    manifest_uri = upload_to_bucket(
        manifest_path,
        bucket,
        manifest_path.name,
        storage_client=storage_client,
    )
    print(f"Wrote side-store manifest to {manifest_uri}")


if __name__ == "__main__":
    main()
