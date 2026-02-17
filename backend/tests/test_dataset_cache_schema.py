"""Unit tests for dataset manifest chunk-schema compatibility checks."""

from __future__ import annotations

import gzip
import io
import json
from typing import Any

import pytest

from api import dataset_cache

MANIFEST_PATH = "datasets/v1/manifest.json"
DATASET_VERSION = "v1"


def _build_manifest_payload(**overrides: Any) -> bytes:
    """Build a minimal valid manifest payload with optional field overrides.

    Inputs:
        **overrides: Field values to override in the default manifest object.

    Outputs:
        UTF-8 encoded manifest JSON bytes suitable for _parse_manifest.

    Edge cases:
        Allows overriding required keys to None or other invalid values so tests
        can assert failure behavior.
    """
    payload: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "chunk_schema_version": dataset_cache.get_supported_chunk_schema_version(),
        "created_at": "2026-01-01T00:00:00Z",
        "datapoints_file": "datapoints.jsonl",
        "chunks_file": "chunks.jsonl.gz",
        "embedding_model": "text-embedding-004",
        "dimensions": 768,
        "num_datapoints": 10,
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_parse_manifest_accepts_supported_chunk_schema_version() -> None:
    """Verify _parse_manifest accepts manifests with the supported chunk schema version.

    What is tested:
        Successful parse path when chunk_schema_version matches runtime support.
    How it's tested:
        Parse a minimal manifest payload built with the supported version.
    Expected result format:
        Returned dict includes chunk_schema_version equal to supported value.
    """
    manifest = dataset_cache._parse_manifest(  # pyright: ignore[reportPrivateUsage]
        _build_manifest_payload(),
        manifest_path=MANIFEST_PATH,
        expected_dataset_version=DATASET_VERSION,
    )

    assert (
        manifest["chunk_schema_version"]
        == dataset_cache.get_supported_chunk_schema_version()
    )


def test_parse_manifest_rejects_missing_chunk_schema_version() -> None:
    """Verify _parse_manifest hard-fails when chunk_schema_version is absent.

    What is tested:
        Mandatory chunk_schema_version enforcement for manifest compatibility.
    How it's tested:
        Remove chunk_schema_version from a manifest payload and parse it.
    Expected result format:
        ChunkSchemaVersionError is raised with no found chunk schema version.
    """
    payload: dict[str, Any] = json.loads(_build_manifest_payload().decode("utf-8"))
    payload.pop("chunk_schema_version", None)

    with pytest.raises(dataset_cache.ChunkSchemaVersionError) as raised_error:
        dataset_cache._parse_manifest(  # pyright: ignore[reportPrivateUsage]
            json.dumps(payload).encode("utf-8"),
            manifest_path=MANIFEST_PATH,
            expected_dataset_version=DATASET_VERSION,
        )

    assert (
        raised_error.value.expected_chunk_schema_version
        == dataset_cache.get_supported_chunk_schema_version()
    )
    assert raised_error.value.found_chunk_schema_version is None


def test_parse_manifest_rejects_unsupported_chunk_schema_version() -> None:
    """Verify _parse_manifest hard-fails when chunk_schema_version is not supported.

    What is tested:
        Strict schema-version mismatch detection during manifest parse.
    How it's tested:
        Parse a manifest payload whose chunk_schema_version is above the supported value.
    Expected result format:
        ChunkSchemaVersionError is raised with the provided found version.
    """
    unsupported_chunk_schema_version = (
        dataset_cache.get_supported_chunk_schema_version() + 1
    )

    with pytest.raises(dataset_cache.ChunkSchemaVersionError) as raised_error:
        dataset_cache._parse_manifest(  # pyright: ignore[reportPrivateUsage]
            _build_manifest_payload(
                chunk_schema_version=unsupported_chunk_schema_version
            ),
            manifest_path=MANIFEST_PATH,
            expected_dataset_version=DATASET_VERSION,
        )

    assert (
        raised_error.value.expected_chunk_schema_version
        == dataset_cache.get_supported_chunk_schema_version()
    )
    assert (
        raised_error.value.found_chunk_schema_version
        == unsupported_chunk_schema_version
    )


def test_load_chunks_accepts_flat_v3_records_without_metadata() -> None:
    """Verify _load_chunks returns flat records keyed by chunk_id.

    What is tested:
        Dataset chunk loading for schema-v3 flat runtime records.
    How it's tested:
        Build a gzipped one-line chunks payload with chunk_id/text/profile fields.
    Expected result format:
        Mapping contains the chunk by chunk_id with no nested metadata field.
    """
    chunk_record = {
        "schema_version": dataset_cache.get_supported_chunk_schema_version(),
        "doc_id": "doc-1",
        "chunk_id": "doc-1:01",
        "position": 1,
        "text": "Flat runtime chunk",
        "profile": "infra",
        "section": "Experience",
    }
    payload_buffer = io.BytesIO()
    with gzip.open(payload_buffer, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(chunk_record))
        handle.write("\n")

    loaded_chunks = dataset_cache._load_chunks(  # pyright: ignore[reportPrivateUsage]
        payload_buffer.getvalue(),
        "datasets/v1/chunks.jsonl.gz",
    )

    loaded_chunk = loaded_chunks["doc-1:01"]
    assert loaded_chunk["chunk_id"] == "doc-1:01"
    assert "metadata" not in loaded_chunk


def test_load_chunks_rejects_legacy_metadata_field() -> None:
    """Verify _load_chunks rejects legacy nested metadata chunk records.

    What is tested:
        Strict flat-schema enforcement for chunk payloads.
    How it's tested:
        Build a gzipped chunks payload containing a metadata field.
    Expected result format:
        RuntimeError is raised with rebuild guidance.
    """
    legacy_chunk_record = {
        "chunk_id": "legacy-1",
        "text": "Legacy chunk",
        "metadata": {"section": "Experience"},
    }
    payload_buffer = io.BytesIO()
    with gzip.open(payload_buffer, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(legacy_chunk_record))
        handle.write("\n")

    with pytest.raises(RuntimeError, match="Rebuild dataset artifacts with pack_and_push"):
        dataset_cache._load_chunks(  # pyright: ignore[reportPrivateUsage]
            payload_buffer.getvalue(),
            "datasets/v1/chunks.jsonl.gz",
        )
