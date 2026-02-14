"""Tests for /ready readiness payloads and startup error diagnostics."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from api import dataset_cache
from api import main as main_app


def test_ready_returns_supported_chunk_schema_version_when_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ready() returns schema support metadata when startup is healthy.

    What is tested:
        Successful readiness payload content.
    How it's tested:
        Set readiness globals to a healthy state and call ready() directly.
    Expected result format:
        Response dict includes ready=true and supported schema version.
    """
    monkeypatch.setattr(main_app, "is_init_done", True)
    monkeypatch.setattr(main_app, "is_ready", True)
    monkeypatch.setattr(main_app, "loaded_chunk_schema_version", 1)

    response: dict[str, Any] = main_app.ready()

    assert response["ready"] is True
    assert (
        response["supported_chunk_schema_version"]
        == dataset_cache.get_supported_chunk_schema_version()
    )
    assert response["loaded_chunk_schema_version"] == 1


def test_ready_exposes_chunk_schema_unsupported_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ready() exposes explicit schema hard-fail diagnostics.

    What is tested:
        503 payload fields for startup schema incompatibility.
    How it's tested:
        Set readiness globals to a chunk_schema_unsupported failure and call ready().
    Expected result format:
        HTTPException detail includes code/message and supported schema version.
    """
    monkeypatch.setattr(main_app, "is_init_done", True)
    monkeypatch.setattr(main_app, "is_ready", False)
    monkeypatch.setattr(main_app, "startup_error_code", "chunk_schema_unsupported")
    monkeypatch.setattr(main_app, "startup_error_message", "Unsupported chunk schema version")
    monkeypatch.setattr(main_app, "loaded_chunk_schema_version", None)

    with pytest.raises(HTTPException) as raised_error:
        main_app.ready()

    detail = raised_error.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "chunk_schema_unsupported"
    assert detail["message"] == "Unsupported chunk schema version"
    assert (
        detail["supported_chunk_schema_version"]
        == dataset_cache.get_supported_chunk_schema_version()
    )
    assert detail["loaded_chunk_schema_version"] is None
