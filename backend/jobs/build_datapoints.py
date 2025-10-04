"""Generate Vertex AI Matching Engine datapoints from persona chunks."""

from __future__ import annotations

import argparse
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
    resolve_existing_path,
)


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

    env = load_backend_env(["PROJECT_ID", "REGION"])
    default_schema = backend_root / "schema" / "chunk.schema.json"
    default_input = repo_root / "private" / "persona" / "data" / "chunks.jsonl"
    default_output = repo_root / "private" / "persona" / "data" / "datapoints.jsonl"

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default=str(default_schema))
    parser.add_argument("--input", default=str(default_input))
    parser.add_argument("--output", default=str(default_output))
    parser.add_argument("--model", default="text-embedding-004")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-chars", type=int, default=2200)
    parser.add_argument("--gzip", action="store_true", help="Compress the datapoints file")
    args = parser.parse_args()

    schema_path = resolve_existing_path(args.schema, backend_root)
    input_path = resolve_existing_path(args.input, repo_root, backend_root)
    output_path = Path(args.output)
    if args.gzip and not output_path.suffix.endswith(".gz"):
        output_path = output_path.with_suffix(output_path.suffix + ".gz")

    records = build_persona_records(schema_path, input_path, max_chars=args.max_chars)
    if not records:
        raise RuntimeError("No persona records found; check the input file")

    project_id = env["PROJECT_ID"]
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GCLOUD_PROJECT"] = project_id
    os.environ["CLOUD_ML_PROJECT_ID"] = project_id

    vertexai.init(project=project_id, location=env["REGION"])
    print(f"Using Vertex project '{project_id}' in region '{env['REGION']}'")
    model = TextEmbeddingModel.from_pretrained(args.model)

    embeddings: list[List[float]] = []
    for batch in _batched(records, args.batch_size):
        texts = [record["text"] for record in batch]
        responses = model.get_embeddings(texts)
        embeddings.extend(_embedding_values(resp) for resp in responses)

    if len(embeddings) != len(records):
        raise RuntimeError(
            "Embedding response count did not match records; aborting without writing datapoints"
        )

    _write_datapoints(records, embeddings, output_path, gzip_output=args.gzip)

    total = len(records)
    dims = len(next(iter(embeddings))) if embeddings else 0
    print(f"Wrote {total} datapoints ({dims} dims) to {output_path}")
    print("Ready for make gcp-index-upsert DATAPOINTS_FILE=...\n")


if __name__ == "__main__":
    main()
