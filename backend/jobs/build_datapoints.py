"""Generate Vertex AI Matching Engine datapoints from persona chunks."""

from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Mapping, MutableMapping, Sequence, cast

import vertexai
from vertexai.language_models import TextEmbeddingModel

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


def _env_bool(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default)
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Invalid integer for {name}: {raw}") from exc


def _batched(items: Sequence[dict[str, object]], size: int) -> Iterator[Sequence[dict[str, object]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _build_restricts(metadata: Mapping[str, object]) -> list[dict[str, object]]:
    restricts: list[dict[str, object]] = []

    role = metadata.get("role")
    if isinstance(role, str) and role:
        restricts.append({"namespace": "role", "allowTokens": [role]})

    doc_id = metadata.get("doc_id")
    if isinstance(doc_id, str) and doc_id:
        restricts.append({"namespace": "doc_id", "allowTokens": [doc_id]})

    topics = metadata.get("topics")
    if isinstance(topics, list) and topics:
        restricts.append({"namespace": "topic", "allowTokens": topics})

    tags = metadata.get("tags")
    if isinstance(tags, list) and tags:
        restricts.append({"namespace": "tag", "allowTokens": tags})

    return restricts


def _embedding_values(embedding: object) -> List[float]:
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
    handle_fn = gzip.open if gzip_output else open
    mode = "wt"
    with handle_fn(output_path, mode, encoding="utf-8") as handle:
        for record, vector in zip(records, embeddings):
            metadata_obj = record.get("metadata") or {}
            metadata_dict: MutableMapping[str, object]
            if isinstance(metadata_obj, Mapping):
                metadata_dict = dict(metadata_obj)
            else:
                metadata_dict = {}

            restricts = _build_restricts(metadata_dict)
            datapoint: dict[str, object] = {
                "datapointId": str(record["id"]),
                "featureVector": list(vector),
            }
            section = metadata_dict.get("section")
            if isinstance(section, str) and section:
                datapoint["crowdingTag"] = section
            if restricts:
                datapoint["restricts"] = restricts
            handle.write(json.dumps(datapoint, ensure_ascii=False))
            handle.write("\n")


def main() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    repo_root = backend_root.parent

    env = load_backend_env(
        ["PROJECT_ID", "REGION"], optional=["GOOGLE_APPLICATION_CREDENTIALS"]
    )
    default_schema = backend_root / "schema" / "chunk.schema.json"
    private_dir = Path(os.environ.get("PRIVATE_DIR", repo_root / "private")).expanduser()
    default_input = private_dir / "persona" / "data" / "chunks.jsonl"

    env_output = os.getenv("DATAPOINTS_FILE")
    if not env_output:
        raise RuntimeError(
            "DATAPOINTS_FILE must be set in the environment (configure it in backend.env)"
        )
    output_path = Path(env_output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gzip_output = _env_bool("DATAPOINTS_GZIP", "0")
    if gzip_output and not output_path.suffix.endswith(".gz"):
        output_path = output_path.with_suffix(output_path.suffix + ".gz")

    schema_override = os.getenv("DATAPOINTS_SCHEMA")
    input_override = os.getenv("DATAPOINTS_INPUT")

    schema_path = resolve_existing_path(
        schema_override or str(default_schema), backend_root
    )
    input_path = resolve_existing_path(
        input_override or str(default_input), repo_root, backend_root
    )

    max_chars = _env_int("DATAPOINTS_MAX_CHARS", 2200)

    records = build_persona_records(schema_path, input_path, max_chars=max_chars)
    if not records:
        raise RuntimeError("No persona records found; check the input file")

    project_id = env["PROJECT_ID"]
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GCLOUD_PROJECT"] = project_id
    os.environ["CLOUD_ML_PROJECT_ID"] = project_id

    maybe_set_service_account(env)

    vertexai.init(project=project_id, location=env["REGION"])
    print(f"Using Vertex project '{project_id}' in region '{env['REGION']}'")
    model_name = os.getenv("DATAPOINTS_MODEL", "text-embedding-004")
    model = TextEmbeddingModel.from_pretrained(model_name)

    embeddings: list[List[float]] = []
    batch_size = _env_int("DATAPOINTS_BATCH_SIZE", 16)
    for batch in _batched(records, batch_size):
        texts = [record["text"] for record in batch]
        responses = model.get_embeddings(texts)
        embeddings.extend(_embedding_values(resp) for resp in responses)

    if len(embeddings) != len(records):
        raise RuntimeError(
            "Embedding response count did not match records; aborting without writing datapoints"
        )

    _write_datapoints(records, embeddings, output_path, gzip_output=gzip_output)

    total = len(records)
    dims = len(next(iter(embeddings))) if embeddings else 0
    print(f"Wrote {total} datapoints ({dims} dims) to {output_path}")
    print("Ready for make gcp-index-upsert (uses DATAPOINTS_FILE from backend.env)\n")


if __name__ == "__main__":
    main()
