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
import math
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
PROFILE_FIELD = "profile"
PROFILE_MARKETING_VALUE = "marketing"
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
VECTOR_RELATIVE_TOLERANCE = 1e-9
VECTOR_ABSOLUTE_TOLERANCE = 1e-9

MetadataValue = str | list[str]
Metadata = dict[str, MetadataValue]
Record = dict[str, Any]


def _assert_float_vectors_close(
    actual_vector: list[float], expected_vector: list[float]
) -> None:
    """Assert two float vectors are equal within a strict tolerance.

    What it does:
        Compares corresponding elements from two vectors using ``math.isclose``.
    Inputs:
        actual_vector: Float values emitted by datapoint serialization.
        expected_vector: Float values computed by normalization helper.
    Outputs:
        None. Raises ``AssertionError`` when a mismatch is found.
    Edge cases:
        Fails fast when lengths differ to avoid silent truncation during zip.
    Concurrency/atomicity:
        Pure assertion helper with no shared state.
    """
    assert len(actual_vector) == len(expected_vector)
    for actual_value, expected_value in zip(actual_vector, expected_vector):
        assert math.isclose(
            actual_value,
            expected_value,
            rel_tol=VECTOR_RELATIVE_TOLERANCE,
            abs_tol=VECTOR_ABSOLUTE_TOLERANCE,
        )


def test_build_restricts_includes_all_supported_namespaces() -> None:
    """Verify restricts include all supported namespaces.

    What is tested:
        _build_restricts mapping for role, doc_id, topics, and tags.
    How it's tested:
        Build metadata with all supported fields and compare restricts output.
    Expected result format:
        The restricts list matches the expected namespace/token mapping order.
    """
    metadata: Metadata = {
        PROFILE_FIELD: ROLE_VALUE,
        DOC_ID_FIELD: DOC_ID_VALUE,
        TOPICS_FIELD: [KUBERNETES_TOPIC, GKE_TOPIC],
        TAG_FIELD: [PROD_TAG, HIGHLIGHTS_TAG],
    }

    restricts = build_datapoints._build_restricts(metadata) # pyright: ignore[reportPrivateUsage]

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


def test_build_restricts_uses_profile_for_role_namespace() -> None:
    """Verify profile is the only source for the role restrict namespace."""
    metadata: Metadata = {
        PROFILE_FIELD: PROFILE_MARKETING_VALUE,
    }

    restricts = build_datapoints._build_restricts(metadata) # pyright: ignore[reportPrivateUsage]

    assert restricts == [
        {NAMESPACE_FIELD: ROLE_FIELD, ALLOW_TOKENS_FIELD: [PROFILE_MARKETING_VALUE]}
    ]


def test_write_datapoints_emits_expected_json_lines(tmp_path: Path) -> None:
    """Verify JSONL datapoints include IDs, restricts, and embeddings.

    What is tested:
        _write_datapoints JSONL output for a single record with metadata.
    How it's tested:
        Write one record to disk, read the JSON line, and inspect fields.
    Expected result format:
        Output has one datapoint with expected ids, restricts, and featureVector.
    """
    output_path = tmp_path / JSON_LINES_FILENAME

    records: list[Record] = [
        {
            ID_FIELD: CHUNK_ID_ONE,
            METADATA_FIELD: {
                SECTION_FIELD: PROFILE_SECTION,
                PROFILE_FIELD: ROLE_VALUE,
                DOC_ID_FIELD: DOC_ID_VALUE,
                TOPICS_FIELD: [KUBERNETES_TOPIC],
                TAG_FIELD: [PROD_TAG],
            },
        }
    ]
    embeddings = EMBEDDINGS_FOR_SINGLE_RECORD

    build_datapoints._write_datapoints( # pyright: ignore[reportPrivateUsage]
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
    normalized_vector = build_datapoints._l2_normalize( # pyright: ignore[reportPrivateUsage]
        EMBEDDINGS_FOR_SINGLE_RECORD[0]
    )
    _assert_float_vectors_close(datapoint[FEATURE_VECTOR_FIELD], normalized_vector)
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
    """Verify gzip output emits compact datapoints payloads.

    What is tested:
        _write_datapoints gzip mode behavior for minimal records.
    How it's tested:
        Write to a .gz file and parse the single JSON line.
    Expected result format:
        Output has only id fields and featureVector matching the embeddings.
    """
    output_path = tmp_path / GZIP_OUTPUT_FILENAME

    records: list[Record] = [{ID_FIELD: CHUNK_ID_TWO, METADATA_FIELD: {}}]
    embeddings = GZIP_EMBEDDINGS

    build_datapoints._write_datapoints( # pyright: ignore[reportPrivateUsage]
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
        FEATURE_VECTOR_FIELD: build_datapoints._l2_normalize( # pyright: ignore[reportPrivateUsage]
            GZIP_EMBEDDINGS[0]
        ),
    }
