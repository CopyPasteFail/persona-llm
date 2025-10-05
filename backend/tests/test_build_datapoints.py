"""Unit tests for datapoint generation helpers.

These tests exercise the private helpers in ``jobs.build_datapoints`` so that
future refactors stay compatible with Vertex AI Matching Engine expectations.
Checks include:
  * Namespace restriction mapping via ``_build_restricts``.
  * JSON line emission with metadata and feature vectors via ``_write_datapoints``.
  * Optional gzip output path to ensure compression stays supported.
"""

from __future__ import annotations

import gzip
import json

from jobs import build_datapoints


def test_build_restricts_includes_all_supported_namespaces():
    metadata = {
        "role": "infra",
        "doc_id": "resume-2024",
        "topics": ["kubernetes", "gke"],
        "tags": ["prod", "highlights"],
    }

    restricts = build_datapoints._build_restricts(metadata)

    assert restricts == [
        {"namespace": "role", "allowTokens": ["infra"]},
        {"namespace": "doc_id", "allowTokens": ["resume-2024"]},
        {"namespace": "topic", "allowTokens": ["kubernetes", "gke"]},
        {"namespace": "tag", "allowTokens": ["prod", "highlights"]},
    ]


def test_write_datapoints_emits_expected_json_lines(tmp_path):
    output_path = tmp_path / "datapoints.jsonl"

    records = [
        {
            "id": "chunk-1",
            "metadata": {
                "section": "profile",
                "role": "infra",
                "doc_id": "resume-2024",
                "topics": ["kubernetes"],
                "tags": ["prod"],
            },
        }
    ]
    embeddings = [[0.5, 0.5, 0.707106]]

    build_datapoints._write_datapoints(
        records,
        embeddings,
        output_path,
        gzip_output=False,
    )

    with output_path.open("rt", encoding="utf-8") as handle:
        serialized = [json.loads(line) for line in handle if line.strip()]

    assert len(serialized) == 1
    datapoint = serialized[0]
    assert datapoint["datapointId"] == "chunk-1"
    assert datapoint["featureVector"] == [0.5, 0.5, 0.707106]
    assert datapoint["crowdingTag"] == "profile"
    assert datapoint["restricts"] == [
        {"namespace": "role", "allowTokens": ["infra"]},
        {"namespace": "doc_id", "allowTokens": ["resume-2024"]},
        {"namespace": "topic", "allowTokens": ["kubernetes"]},
        {"namespace": "tag", "allowTokens": ["prod"]},
    ]


def test_write_datapoints_handles_gzip_output(tmp_path):
    output_path = tmp_path / "datapoints.jsonl.gz"

    records = [{"id": "chunk-2", "metadata": {}}]
    embeddings = [[0.1, 0.2]]

    build_datapoints._write_datapoints(
        records,
        embeddings,
        output_path,
        gzip_output=True,
    )

    with gzip.open(output_path, "rt", encoding="utf-8") as handle:
        serialized = [json.loads(line) for line in handle if line.strip()]

    assert len(serialized) == 1
    datapoint = serialized[0]
    assert datapoint == {
        "datapointId": "chunk-2",
        "featureVector": [0.1, 0.2],
    }
