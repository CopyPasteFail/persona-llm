"""Unit tests for datapoint generation helpers.

These tests exercise the private helpers in ``jobs.build_datapoints`` so that
future refactors stay compatible with Vertex AI Matching Engine expectations.
Checks include:
  * Namespace restriction mapping via ``_build_restricts``.
  * JSON line emission with metadata and feature vectors via ``_write_datapoints``.
  * Optional gzip output path to ensure compression stays supported.
"""

import gzip
import json
from pathlib import Path
from typing import Any

from jobs import build_datapoints

ALLOW_TOKENS_FIELD = "allowTokens"
DOC_ID_FIELD = "doc_id"
DOC_ID_VALUE = "resume-2024"
FEATURE_VECTOR_FIELD = "featureVector"
GZIP_OUTPUT_FILENAME = "datapoints.jsonl.gz"
ID_FIELD = "id"
JSON_LINES_FILENAME = "datapoints.jsonl"
KUBERNETES_TOPIC = "kubernetes"
METADATA_FIELD = "metadata"
NAMESPACE_FIELD = "namespace"
PROFILE_SECTION = "profile"
PROD_TAG = "prod"
RESTRICTS_FIELD = "restricts"
ROLE_FIELD = "role"
ROLE_VALUE = "infra"
SECTION_FIELD = "section"
TAG_NAMESPACE = "tag"
TAG_FIELD = "tags"
TOPIC_NAMESPACE = "topic"
TOPICS_FIELD = "topics"
CHUNK_ID_ONE = "chunk-1"
CHUNK_ID_TWO = "chunk-2"
GKE_TOPIC = "gke"
HIGHLIGHTS_TAG = "highlights"
DATAPOINT_ID_FIELD = "datapointId"
CROWDING_TAG_FIELD = "crowdingTag"
EMBEDDINGS_FOR_SINGLE_RECORD = [[0.5, 0.5, 0.707106]]
GZIP_EMBEDDINGS = [[0.1, 0.2]]

MetadataValue = str | list[str]
Metadata = dict[str, MetadataValue]
Record = dict[str, Any]


def test_build_restricts_includes_all_supported_namespaces() -> None:
    """Verifies restricts include every supported namespace by passing rich metadata
    and expecting a complete, ordered restriction list.
    """
    metadata: Metadata = {
        ROLE_FIELD: ROLE_VALUE,
        DOC_ID_FIELD: DOC_ID_VALUE,
        TOPICS_FIELD: [KUBERNETES_TOPIC, GKE_TOPIC],
        TAG_FIELD: [PROD_TAG, HIGHLIGHTS_TAG],
    }

    restricts = build_datapoints._build_restricts(metadata)

    assert restricts == [
        {NAMESPACE_FIELD: ROLE_FIELD, ALLOW_TOKENS_FIELD: [ROLE_VALUE]},
        {NAMESPACE_FIELD: DOC_ID_FIELD, ALLOW_TOKENS_FIELD: [DOC_ID_VALUE]},
        {
            NAMESPACE_FIELD: TOPIC_NAMESPACE,
            ALLOW_TOKENS_FIELD: [KUBERNETES_TOPIC, GKE_TOPIC],
        },
        {
            NAMESPACE_FIELD: TAG_NAMESPACE,
            ALLOW_TOKENS_FIELD: [PROD_TAG, HIGHLIGHTS_TAG],
        },
    ]


def test_write_datapoints_emits_expected_json_lines(tmp_path: Path) -> None:
    """Writes a single record to JSONL and validates the output line includes
    expected IDs, metadata-derived fields, and the provided embedding vector.
    """
    output_path = tmp_path / JSON_LINES_FILENAME

    records: list[Record] = [
        {
            ID_FIELD: CHUNK_ID_ONE,
            METADATA_FIELD: {
                SECTION_FIELD: PROFILE_SECTION,
                ROLE_FIELD: ROLE_VALUE,
                DOC_ID_FIELD: DOC_ID_VALUE,
                TOPICS_FIELD: [KUBERNETES_TOPIC],
                TAG_FIELD: [PROD_TAG],
            },
        }
    ]
    embeddings = EMBEDDINGS_FOR_SINGLE_RECORD

    build_datapoints._write_datapoints(
        records,
        embeddings,
        output_path,
        gzip_output=False,
    )

    with output_path.open("rt", encoding="utf-8") as handle:
        serialized = [json.loads(line) for line in handle if line.strip()]

    assert len(serialized) == 1
    datapoint: dict[str, Any] = serialized[0]
    assert datapoint[DATAPOINT_ID_FIELD] == CHUNK_ID_ONE
    assert datapoint[ID_FIELD] == CHUNK_ID_ONE
    assert datapoint[FEATURE_VECTOR_FIELD] == EMBEDDINGS_FOR_SINGLE_RECORD[0]
    assert datapoint[CROWDING_TAG_FIELD] == PROFILE_SECTION
    assert datapoint[RESTRICTS_FIELD] == [
        {NAMESPACE_FIELD: ROLE_FIELD, ALLOW_TOKENS_FIELD: [ROLE_VALUE]},
        {NAMESPACE_FIELD: DOC_ID_FIELD, ALLOW_TOKENS_FIELD: [DOC_ID_VALUE]},
        {
            NAMESPACE_FIELD: TOPIC_NAMESPACE,
            ALLOW_TOKENS_FIELD: [KUBERNETES_TOPIC],
        },
        {
            NAMESPACE_FIELD: TAG_NAMESPACE,
            ALLOW_TOKENS_FIELD: [PROD_TAG],
        },
    ]


def test_write_datapoints_handles_gzip_output(tmp_path: Path) -> None:
    """Writes a single record to a gzip file and expects a compact JSON
    payload with only IDs and the feature vector.
    """
    output_path = tmp_path / GZIP_OUTPUT_FILENAME

    records: list[Record] = [{ID_FIELD: CHUNK_ID_TWO, METADATA_FIELD: {}}]
    embeddings = GZIP_EMBEDDINGS

    build_datapoints._write_datapoints(
        records,
        embeddings,
        output_path,
        gzip_output=True,
    )

    with gzip.open(output_path, "rt", encoding="utf-8") as handle:
        serialized = [json.loads(line) for line in handle if line.strip()]

    assert len(serialized) == 1
    datapoint: dict[str, Any] = serialized[0]
    assert datapoint == {
        DATAPOINT_ID_FIELD: CHUNK_ID_TWO,
        ID_FIELD: CHUNK_ID_TWO,
        FEATURE_VECTOR_FIELD: GZIP_EMBEDDINGS[0],
    }
