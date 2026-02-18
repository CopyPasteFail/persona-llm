"""Helpers for deriving supported chunk schema version from JSON schema files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

_EXPECTED_SCHEMA_VERSION_KEY_PATH = "properties.schema_version.const"
_SUPPORTED_VERSION_CACHE: dict[Path, int] = {}


def get_supported_chunk_schema_version(schema_path: Path) -> int:
    """Load and cache the supported chunk schema version from a JSON schema file.

    Inputs:
        schema_path: Path to a chunk schema JSON file.

    Outputs:
        Integer value from ``properties.schema_version.const``.

    Edge cases:
        Raises RuntimeError when the file cannot be read, parsed, or does not
        define an integer const at the expected key path.
    """
    resolved_schema_path = schema_path.expanduser().resolve()
    cached_version = _SUPPORTED_VERSION_CACHE.get(resolved_schema_path)
    if cached_version is not None:
        return cached_version

    if not resolved_schema_path.is_file():
        raise RuntimeError(
            "Unable to load chunk schema version from "
            f"{resolved_schema_path}: expected integer at "
            f"{_EXPECTED_SCHEMA_VERSION_KEY_PATH}; found <missing file>."
        )

    try:
        schema_payload = json.loads(
            resolved_schema_path.read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise RuntimeError(
            "Unable to read chunk schema file for schema version: "
            f"{resolved_schema_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Unable to parse chunk schema JSON for schema version: "
            f"{resolved_schema_path}"
        ) from exc

    found_value = _extract_schema_version_const(schema_payload)
    if not isinstance(found_value, int):
        raise RuntimeError(
            "Unable to load chunk schema version from "
            f"{resolved_schema_path}: expected integer at "
            f"{_EXPECTED_SCHEMA_VERSION_KEY_PATH}; found {found_value!r}."
        )

    _SUPPORTED_VERSION_CACHE[resolved_schema_path] = found_value
    return found_value


def _extract_schema_version_const(schema_payload: Any) -> Any:
    """Return the best-effort value at ``properties.schema_version.const``.

    Inputs:
        schema_payload: Parsed JSON schema payload.

    Outputs:
        Value found at the expected key path, or the closest encountered value.

    Edge cases:
        Returns sentinel strings when key path segments are missing.
    """
    if not isinstance(schema_payload, Mapping):
        return schema_payload

    properties = schema_payload.get("properties")
    if not isinstance(properties, Mapping):
        return properties

    schema_version_property = properties.get("schema_version")
    if not isinstance(schema_version_property, Mapping):
        return schema_version_property

    if "const" not in schema_version_property:
        return "<missing const>"

    return schema_version_property.get("const")
