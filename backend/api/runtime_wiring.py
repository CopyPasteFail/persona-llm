"""Shared runtime wiring helpers for integrated retrieval setup."""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Protocol

from . import dataset_cache

DEFAULT_EMBEDDING_MODEL_NAME = "text-embedding-004"


class IntegratedRetrievalModule(Protocol):
    """Protocol for retrieval module functions used by integrated startup wiring."""

    def configure_vertex_embedding_client(
        self, *, project: str, region: str, model_name: str
    ) -> None: ...

    def configure_vector_client(self, client: Any | None) -> None: ...

    def configure_chunk_store(
        self,
        chunks: (
            Mapping[str, Mapping[str, Any]]
            | Iterable[Mapping[str, Any]]
            | None
        ),
    ) -> None: ...


def resolve_embedding_model_name() -> str:
    """Resolve embedding model name using the same precedence as integrated app startup.

    Inputs:
    - None. Reads process environment variables.

    Outputs:
    - Embedding model name string.

    Edge cases:
    - Falls back to `text-embedding-004` when no embedding env var is set.

    Concurrency/atomicity:
    - Pure read-only environment lookup; no shared-state mutation.
    """

    return (
        os.getenv("EMBEDDING_MODEL")
        or os.getenv("DATAPOINTS_MODEL")
        or DEFAULT_EMBEDDING_MODEL_NAME
    )


def configure_integrated_retrieval_runtime(
    *,
    retrieval_module: IntegratedRetrievalModule,
    project_id: str,
    region: str,
) -> dataset_cache.DatasetCache:
    """Configure retrieval exactly like the integrated API startup path.

    Inputs:
    - retrieval_module: Retrieval module implementing embedding/vector/chunk setup functions.
    - project_id: GCP project id used by Vertex embedding client.
    - region: GCP region used by Vertex embedding client.

    Outputs:
    - Freshly loaded dataset cache snapshot used to hydrate retrieval chunk storage.

    Edge cases:
    - Raises if Vertex embedding client setup fails.
    - Raises if dataset cache reload fails due to missing/invalid dataset artifacts.

    Concurrency/atomicity:
    - Replaces retrieval module dependencies before loading chunk store; callers
      should invoke this during startup, before request handling or batch loops.
    """

    embedding_model_name = resolve_embedding_model_name()
    retrieval_module.configure_vertex_embedding_client(
        project=project_id,
        region=region,
        model_name=embedding_model_name,
    )
    cache = dataset_cache.reload_cache()
    retrieval_module.configure_chunk_store(cache.chunks_by_id)
    return cache
