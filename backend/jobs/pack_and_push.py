"""Validate persona chunk data, bundle it into a gzipped JSONL file, and upload the artifact to Cloud Storage.

The script reads persona chunk definitions, enforces the JSON schema, splits long entries
into sentence-based fragments, writes a deterministic filename, and pushes the result to
the configured GCS bucket so downstream services can consume the latest persona content."""

import argparse
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable, cast

from dotenv import dotenv_values
from jsonschema import Draft202012Validator
from google.cloud import storage


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


def load_backend_env(keys: list[str]) -> dict[str, str]:
    """Load selected backend secrets from the private directory."""
    private_dir = os.getenv("PRIVATE_DIR")
    if not private_dir:
        raise RuntimeError("PRIVATE_DIR is not set. It must point to the private folder.")

    env_path = Path(private_dir).expanduser().resolve() / "secrets" / "backend.env"
    if not env_path.exists():
        raise RuntimeError(f"Missing secrets file: {env_path}")

    raw_env: Mapping[str, str | None] = cast(Mapping[str, str | None], dotenv_values(env_path))
    env_values: dict[str, str] = {k: v for k, v in raw_env.items() if v}
    selected: dict[str, str] = {}
    for key in keys:
        value = os.getenv(key) or env_values.get(key)
        if not value:
            raise RuntimeError(f"Missing required env var: {key}")
        selected[key] = value
    return selected



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
    out: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= max_chars:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                out.append(cur)
            cur = s
    if cur:
        out.append(cur)
    return out

def deterministic_id(text: str) -> str:
    """Generate a stable 12-character hex fragment derived from `text`."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

def main():
    """Validate persona chunks, bundle them, and push the archive to GCS."""
    backend_root = Path(__file__).resolve().parent.parent
    repo_root = backend_root.parent
    env = load_backend_env(["BUCKET_NAME"])

    default_schema = backend_root / "schema" / "chunk.schema.json"
    default_input = repo_root / "private" / "persona" / "data" / "chunks.jsonl"

    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default=str(default_schema))
    ap.add_argument("--input", default=str(default_input))
    args = ap.parse_args()

    schema_path = resolve_existing_path(args.schema, backend_root)
    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    records: list[dict[str, object]] = []
    input_path = resolve_existing_path(args.input, repo_root, backend_root)
    validator = cast(_JsonSchemaValidator, Draft202012Validator(schema))

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            validator.validate(obj)

            base_id = obj.get("id") or f"cv:auto:{deterministic_id(obj.get('text', ''))}"
            metadata = cast(dict[str, object], obj.get("metadata", {}))

            chunks = split_sentences(obj["text"], 2200)
            if len(chunks) == 1:
                records.append({"id": base_id, "text": obj["text"], "metadata": metadata})
            else:
                for i, ch in enumerate(chunks):
                    rid = f"{base_id}:{i:02d}"
                    records.append({"id": rid, "text": ch, "metadata": metadata})

    data = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode("utf-8")
    sha = hashlib.sha1(data).hexdigest()[:12]
    out_name = f"chunks-{sha}.jsonl.gz"
    out_path = Path(out_name)
    with gzip.open(out_path, "wb") as gz:
        gz.write(data)

    bucket = env["BUCKET_NAME"]
    uri = upload_to_bucket(out_path, bucket, out_name)
    print(f"Uploaded persona chunks to {uri}")
    print(f"Artifact name: {out_name} (set this as CHUNKS_PATH in your backend env)")

if __name__ == "__main__":
    main()
