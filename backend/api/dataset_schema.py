"""Dataset schema compatibility helpers shared by runtime and build jobs."""

from __future__ import annotations

import os
from pathlib import Path

from .chunk_schema_version import (
    get_supported_chunk_schema_version as _get_supported_chunk_schema_version,
)

_CHUNK_SCHEMA_PATH_ENV_VAR = "CHUNK_SCHEMA_PATH"
_DEFAULT_CHUNK_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "schema" / "chunk.schema.json"
)


def get_chunk_schema_path() -> Path:
    """Resolve the chunk schema path used for runtime schema-version checks.

    Inputs:
        None.

    Outputs:
        Resolved path to the chunk schema file.

    Edge cases:
        Uses CHUNK_SCHEMA_PATH when set, otherwise defaults to the repo schema
        path. Raises RuntimeError when the resolved path does not exist.
    """
    configured_path = os.getenv(_CHUNK_SCHEMA_PATH_ENV_VAR)
    candidate_path = (
        Path(configured_path).expanduser()
        if configured_path
        else _DEFAULT_CHUNK_SCHEMA_PATH
    )
    resolved_path = candidate_path.resolve()
    if not resolved_path.is_file():
        raise RuntimeError(
            "Chunk schema file not found at "
            f"{resolved_path}. Set {_CHUNK_SCHEMA_PATH_ENV_VAR} to "
            "backend/schema/chunk.schema.json."
        )
    return resolved_path


def get_supported_chunk_schema_version() -> int:
    """Return supported chunk schema version derived from chunk.schema.json."""
    return _get_supported_chunk_schema_version(get_chunk_schema_path())
