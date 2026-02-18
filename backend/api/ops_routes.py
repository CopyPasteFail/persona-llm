from __future__ import annotations

import threading
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from . import dataset_cache, retrieval
from .ops_security import require_ops_secret

RELOAD_MIN_INTERVAL_SECONDS = 10.0
RELOAD_RATE_LIMIT_ERROR = "vector_reload_rate_limited"

_reload_lock = threading.Lock()
_last_reload_ts: float | None = None

router = APIRouter()


def _enforce_reload_rate_limit() -> None:
    """Allow at most one reload in the configured interval."""
    now = time.time()
    with _reload_lock:
        global _last_reload_ts
        if _last_reload_ts is not None:
            elapsed = now - _last_reload_ts
            if elapsed < RELOAD_MIN_INTERVAL_SECONDS:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"{RELOAD_RATE_LIMIT_ERROR}: "
                        f"retry after {RELOAD_MIN_INTERVAL_SECONDS - elapsed:.1f}s"
                    ),
                )
        _last_reload_ts = now


@router.get("/ops/vector/status", dependencies=[Depends(require_ops_secret)])
def vector_status() -> Dict[str, Any]:
    """Report the loaded dataset version and the current pointer version."""
    pointer = dataset_cache.get_pointer_info()
    cache = dataset_cache.get_cache_snapshot()
    payload: Dict[str, Any] = {
        "pointer_dataset_version": pointer.dataset_version,
        "pointer_generation": pointer.generation,
        "supported_chunk_schema_version": dataset_cache.get_supported_chunk_schema_version(),
    }
    if cache is None:
        payload["loaded_dataset_version"] = None
        payload["loaded_chunk_schema_version"] = None
        return payload

    payload.update(
        {
            "loaded_dataset_version": cache.dataset_version,
            "loaded_chunk_schema_version": cache.chunk_schema_version,
            "loaded_at": cache.loaded_at,
            "datapoints_generation": cache.datapoints_generation,
            "chunks_generation": cache.chunks_generation,
            "manifest_generation": cache.manifest_generation,
            "num_datapoints": cache.num_datapoints,
            "dimensions": cache.dimensions,
        }
    )
    return payload


@router.post("/ops/vector/reload", dependencies=[Depends(require_ops_secret)])
def vector_reload() -> Dict[str, Any]:
    """Reload the dataset cache from the pointer and return metadata."""
    _enforce_reload_rate_limit()
    cache = dataset_cache.reload_cache()
    retrieval.configure_chunk_store(cache.chunks_by_id)
    return {
        "loaded_dataset_version": cache.dataset_version,
        "loaded_chunk_schema_version": cache.chunk_schema_version,
        "supported_chunk_schema_version": dataset_cache.get_supported_chunk_schema_version(),
        "loaded_at": cache.loaded_at,
        "datapoints_generation": cache.datapoints_generation,
        "chunks_generation": cache.chunks_generation,
        "manifest_generation": cache.manifest_generation,
        "num_datapoints": cache.num_datapoints,
        "dimensions": cache.dimensions,
    }
