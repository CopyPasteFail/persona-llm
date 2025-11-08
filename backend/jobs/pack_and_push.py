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


def resolve_existing_path(path_value: str, *roots: Path) -> Path:
    """Resolve a file path against several candidate roots."""
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
    """Expand env-style tokens like $HOME and ~."""
    return os.path.expanduser(os.path.expandvars(value))


def load_backend_env(
    keys: list[str], *, optional: Iterable[str] | None = None
) -> dict[str, str]:
    """Load selected backend secrets from the private directory."""
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
    """Apply GOOGLE_APPLICATION_CREDENTIALS from env mapping if present."""
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


def upload_to_bucket(file_path: Path, bucket_name: str, object_name: str) -> str:
    """Upload the artifact to Cloud Storage and return its URI."""
    client = cast(_StorageClient, storage.Client())
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(file_path))
    return f"gs://{bucket_name}/{object_name}"


def split_sentences(text: str, max_chars: int = 2200) -> list[str]:
    """Break text into <= max_chars segments, preferring sentence boundaries."""
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
    """Generate a stable 12-character hex fragment derived from `text`."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _load_chunks(input_path: Path, schema: Mapping[str, object]) -> Iterable[dict[str, object]]:
    validator = cast(_JsonSchemaValidator, Draft202012Validator(schema))
    with open(input_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            validator.validate(obj)
            yield cast(dict[str, object], obj)


def _build_metadata(chunk: Mapping[str, object], index: int, total: int) -> MutableMapping[str, object]:
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

    if chunk.get("topics"):
        metadata["topics"] = list(chunk["topics"])
    if chunk.get("tags"):
        metadata["tags"] = list(chunk["tags"])
    if chunk.get("permissions"):
        metadata["permissions"] = list(chunk["permissions"])

    extras = chunk.get("extras")
    if isinstance(extras, dict) and extras:
        metadata["extras"] = dict(extras)

    metadata["fragment_index"] = index
    metadata["fragment_count"] = total
    return metadata


def build_persona_records(
    schema_path: Path,
    input_path: Path,
    *,
    max_chars: int = 2200,
) -> list[dict[str, object]]:
    """Load JSONL chunks, split long entries, and enrich metadata."""

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    records: list[dict[str, object]] = []
    for chunk in _load_chunks(input_path, schema):
        text = cast(str, chunk["text"])
        fragments = split_sentences(text, max_chars=max_chars)
        base_id = cast(str, chunk.get("chunk_id") or chunk.get("id") or deterministic_id(text))
        for idx, fragment in enumerate(fragments):
            fragment_id = base_id if len(fragments) == 1 else f"{base_id}:{idx + 1:02d}"
            metadata = _build_metadata(chunk, idx, len(fragments))
            records.append({"id": fragment_id, "text": fragment, "metadata": metadata})
    return records


def _serialize_records(records: Iterable[dict[str, object]]) -> tuple[bytes, str]:
    content = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode("utf-8")
    digest = hashlib.sha1(content).hexdigest()[:12]
    return content, f"chunks-{digest}.jsonl.gz"


def _sha256_digest(path: Path) -> str:
    """Return the SHA-256 hex digest for the given file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _write_manifest(
    *,
    artifact_path: Path,
    artifact_uri: str,
    schema_path: Path,
    input_path: Path,
    record_count: int,
) -> Path:
    """Emit a JSON manifest describing the generated chunk artifact."""
    checksum = _sha256_digest(artifact_path)
    manifest = {
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
    """Validate persona chunks, bundle them, and push the archive to GCS."""

    backend_root = Path(__file__).resolve().parent.parent
    repo_root = backend_root.parent
    env = load_backend_env(["BUCKET_NAME"], optional=["GOOGLE_APPLICATION_CREDENTIALS"])

    default_schema = backend_root / "schema" / "chunk.schema.json"
    default_input = repo_root / "private" / "persona" / "data" / "chunks.jsonl"

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default=str(default_schema))
    parser.add_argument("--input", default=str(default_input))
    args = parser.parse_args()

    schema_path = resolve_existing_path(args.schema, backend_root)
    input_path = resolve_existing_path(args.input, repo_root, backend_root)

    maybe_set_service_account(env)

    records = build_persona_records(schema_path, input_path)
    payload, filename = _serialize_records(records)

    out_path = Path(filename)
    with gzip.open(out_path, "wb") as gz:
        gz.write(payload)

    bucket = env["BUCKET_NAME"]
    uri = upload_to_bucket(out_path, bucket, filename)
    print(f"Uploaded persona chunks to {uri}")
    print(f"Artifact name: {filename} (set this as CHUNKS_PATH in your backend env)")

    manifest_path = _write_manifest(
        artifact_path=out_path,
        artifact_uri=uri,
        schema_path=schema_path,
        input_path=input_path,
        record_count=len(records),
    )
    manifest_uri = upload_to_bucket(manifest_path, bucket, manifest_path.name)
    print(f"Wrote side-store manifest to {manifest_uri}")


if __name__ == "__main__":
    main()
