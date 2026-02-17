"""Unit tests for chunk schema-version extraction from JSON schema files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.chunk_schema_version import get_supported_chunk_schema_version

_EXPECTED_KEY_PATH = "properties.schema_version.const"


def _write_schema(path: Path, schema_version_const: object) -> None:
    """Write a minimal chunk schema payload with caller-controlled const value."""
    schema_payload = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "schema_version": {
                "type": "integer",
                "const": schema_version_const,
            }
        },
    }
    path.write_text(json.dumps(schema_payload), encoding="utf-8")


def test_get_supported_chunk_schema_version_reads_integer_const(tmp_path: Path) -> None:
    """Schema helper should return integer const from the expected key path."""
    schema_path = tmp_path / "chunk.schema.json"
    _write_schema(schema_path, 7)

    supported_version = get_supported_chunk_schema_version(schema_path)

    assert supported_version == 7


def test_get_supported_chunk_schema_version_raises_when_const_missing(
    tmp_path: Path,
) -> None:
    """Schema helper should fail when properties.schema_version.const is absent."""
    schema_path = tmp_path / "chunk.schema.json"
    schema_payload = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "schema_version": {
                "type": "integer",
            }
        },
    }
    schema_path.write_text(json.dumps(schema_payload), encoding="utf-8")

    with pytest.raises(RuntimeError) as raised_error:
        get_supported_chunk_schema_version(schema_path)

    error_message = str(raised_error.value)
    assert str(schema_path.resolve()) in error_message
    assert _EXPECTED_KEY_PATH in error_message
    assert "<missing const>" in error_message


def test_get_supported_chunk_schema_version_raises_when_const_not_int(
    tmp_path: Path,
) -> None:
    """Schema helper should fail when const exists but is not an integer."""
    schema_path = tmp_path / "chunk.schema.json"
    _write_schema(schema_path, "3")

    with pytest.raises(RuntimeError) as raised_error:
        get_supported_chunk_schema_version(schema_path)

    error_message = str(raised_error.value)
    assert str(schema_path.resolve()) in error_message
    assert _EXPECTED_KEY_PATH in error_message
    assert "'3'" in error_message


def test_get_supported_chunk_schema_version_caches_result_by_path(
    tmp_path: Path,
) -> None:
    """Schema helper should return cached value for repeated reads of one path."""
    schema_path = tmp_path / "chunk.schema.json"
    _write_schema(schema_path, 11)

    first_value = get_supported_chunk_schema_version(schema_path)
    _write_schema(schema_path, 12)
    second_value = get_supported_chunk_schema_version(schema_path)

    assert first_value == 11
    assert second_value == 11
