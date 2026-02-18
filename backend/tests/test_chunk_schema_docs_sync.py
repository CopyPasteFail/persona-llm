"""Guardrail test to keep schema-version examples in docs aligned with schema."""

from __future__ import annotations

import json
import re
from pathlib import Path


SCHEMA_VERSION_EXAMPLE_PATTERN = re.compile(r'"schema_version"\s*:\s*(\d+)')
SCHEMA_JSON_RELATIVE_PATH = Path("schema/chunk.schema.json")
DOC_RELATIVE_PATHS = (
    Path("docs/SCHEMA.md"),
    Path("docs/prompts/CVs_to_JSONL.md"),
)


def _read_schema_version_const(schema_path: Path) -> int:
    """Read integer schema-version const from the chunk JSON schema.

    Inputs:
    - schema_path: Path to `backend/schema/chunk.schema.json`.

    Outputs:
    - Integer value at `properties.schema_version.const`.

    Edge cases:
    - Raises RuntimeError when the expected path is missing or non-integer.
    """

    schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    properties_value = schema_payload.get("properties")
    if not isinstance(properties_value, dict):
        raise RuntimeError("Chunk schema is missing object `properties`.")

    schema_version_property = properties_value.get("schema_version")
    if not isinstance(schema_version_property, dict):
        raise RuntimeError("Chunk schema is missing object `properties.schema_version`.")

    schema_version_const = schema_version_property.get("const")
    if not isinstance(schema_version_const, int):
        raise RuntimeError(
            "Chunk schema `properties.schema_version.const` must be an integer."
        )
    return schema_version_const


def _extract_schema_version_examples(document_text: str) -> list[tuple[int, int]]:
    """Extract `(line_number, version)` pairs for explicit schema_version examples.

    Inputs:
    - document_text: Full text of a docs file to scan.

    Outputs:
    - List of `(line_number, integer_version)` tuples for matches to
      `"schema_version": <int>`.

    Edge cases:
    - Returns an empty list when the docs file has no explicit integer examples.
    """

    matches: list[tuple[int, int]] = []
    for version_match in SCHEMA_VERSION_EXAMPLE_PATTERN.finditer(document_text):
        line_number = document_text.count("\n", 0, version_match.start()) + 1
        matches.append((line_number, int(version_match.group(1))))
    return matches


def test_docs_schema_version_examples_match_chunk_schema_const() -> None:
    """Ensure docs schema-version examples stay aligned with schema source of truth.

    Inputs:
    - `backend/schema/chunk.schema.json` const value.
    - Selected docs files with optional JSON examples.

    Outputs:
    - Assertion that every explicit integer `"schema_version"` example equals the
      schema const; files without explicit integer examples are ignored.

    Edge cases:
    - If a docs file has no integer examples for schema_version, this test passes.
    """

    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    schema_path = backend_root / SCHEMA_JSON_RELATIVE_PATH
    expected_schema_version = _read_schema_version_const(schema_path)

    mismatches: list[str] = []
    for document_relative_path in DOC_RELATIVE_PATHS:
        document_path = repo_root / document_relative_path
        document_text = document_path.read_text(encoding="utf-8")
        for line_number, found_version in _extract_schema_version_examples(document_text):
            if found_version == expected_schema_version:
                continue
            mismatches.append(
                f"{document_relative_path}:{line_number} "
                f"expected {expected_schema_version}, found {found_version}"
            )

    assert not mismatches, (
        "Schema-version examples in docs are out of sync with "
        "`backend/schema/chunk.schema.json`. "
        f"Mismatches: {'; '.join(mismatches)}"
    )
