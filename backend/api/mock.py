"""Mock API app with deterministic LLM and optional deterministic retrieval.

Note: the mock app still runs the full RAG pipeline (embed, vector search,
filtering, then LLM generation). The LLM answer is deterministic, but retrieval
still happens. In deterministic mode, embedding/vector are stubs that return
fixed values, and the chunk store uses _DETERMINISTIC_CHUNKS (not real data).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse
from .settings import settings
from .auth import router as auth_router
from .security import Session, get_current_session
from . import llm_backends, ops_routes, rag_chat_orchestrator, retrieval
from .keys import JsonKeyStore, set_key_store

logger = logging.getLogger("api.mock")
SEARCH_TOP_K = 8

_llm_backend: llm_backends.LlmBackend = llm_backends.get_llm_backend(
    default_backend="deterministic"
)
_DETERMINISTIC_CHUNKS: List[Dict[str, Any]] = [
    {"id": "mock:1", "text": "deterministic mock chunk", "metadata": {}}
]


class _DeterministicEmbeddingClient:
    """Deterministic embedding stub for mock mode."""

    def embed(self, text: str) -> Optional[Sequence[float]]:
        return [1.0]


class _DeterministicVectorClient:
    """Deterministic vector search stub for mock mode."""

    def query(self, embedding: Sequence[float], *, top_k: int) -> List[Dict[str, Any]]:
        return [{"id": "mock:1", "distance": 0.0}]


def _resolve_mock_key_store_path() -> Path | None:
    """Resolve the filesystem path for the mock access key store.

    Returns:
        Path | None: The resolved path to the mock key store if it exists,
        otherwise None.

    Edge cases:
        - If MOCK_ACCESS_KEYS_PATH is set but points to a missing file,
          this returns the expanded path and lets the caller decide.
        - If the default path does not exist, None is returned.
    """
    if settings.MOCK_ACCESS_KEYS_PATH:
        return Path(settings.MOCK_ACCESS_KEYS_PATH).expanduser()
    default_path = Path(__file__).resolve().parents[1] / "mock_access_keys.json"
    return default_path if default_path.exists() else None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Configure the mock app lifespan by loading the key store.

    Args:
        _app: The FastAPI application instance (unused).

    Yields:
        None. This is an async context manager for FastAPI's lifespan.

    Edge cases:
        - If no key store path is available, the lifespan completes
          without registering any key store.
    """
    path = _resolve_mock_key_store_path()
    if not path:
        logger.info("mock key store disabled (no file found).")
    else:
        set_key_store(JsonKeyStore(path))
        logger.info("mock key store enabled: %s", path)

    if isinstance(_llm_backend, llm_backends.DeterministicLlmBackend):
        retrieval.configure_embedding_client(_DeterministicEmbeddingClient())
        retrieval.configure_vector_client(_DeterministicVectorClient())
        retrieval.configure_chunk_store(_DETERMINISTIC_CHUNKS)
    else:
        model_name = (
            os.getenv("EMBEDDING_MODEL")
            or os.getenv("DATAPOINTS_MODEL")
            or "text-embedding-004"
        )
        retrieval.configure_vertex_embedding_client(
            project=settings.PROJECT_ID,
            region=settings.REGION,
            model_name=model_name,
        )
        retrieval.configure_vector_client(None)
        retrieval.configure_chunk_store(None)

    yield


app = FastAPI(title="Persona LLM API (mock)", version="0.0.0-mock", lifespan=lifespan)

# CORS for local dev
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(ops_routes.router)

@app.get("/health")
def health():
    """Return a basic health indicator for the mock service.

    Returns:
        dict: JSON payload containing a static "ok" status.
    """
    return {"status": "ok"}


@app.post("/chat", response_model_exclude_none=True)
def chat(
    req: ChatRequest,
    _session: Session = Depends(get_current_session),
) -> ChatResponse:
    """Handle a mock chat request and return a deterministic response.

    Args:
        req: The chat request containing a required question string.
        _session: The authenticated session dependency (unused).

    Returns:
        ChatResponse: A fixed-structure response with a mock answer,
        usage counts, and a dummy citation.

    Edge cases:
        - Empty or whitespace-only questions are normalized to an empty
          string before response generation.
        - Extra request fields are ignored by request validation.

    Concurrency:
        - This handler is pure and stateless; it is safe to call
          concurrently and performs no shared-state mutations.
    """
    chat_result = rag_chat_orchestrator.run_rag_chat(
        req.question,
        retrieval=retrieval,
        llm_backend=_llm_backend,
        top_k=SEARCH_TOP_K,
        persona_name=settings.PERSONA_NAME,
        max_input_tokens=settings.MAX_INPUT_TOKENS,
        max_output_tokens=settings.MAX_OUTPUT_TOKENS,
        enable_thinking_gating=settings.ENABLE_THINKING_GATING,
        default_thinking_budget_tokens=settings.THINKING_BUDGET_TOKENS,
        enable_signal_gating=settings.ENABLE_SIGNAL_GATING,
        weighted_score_threshold=settings.WEIGHTED_SCORE_THRESHOLD,
        bm25_threshold=settings.BM25_THRESHOLD,
    )
    response = chat_result.response
    response.model = settings.LLM_MODEL_NAME
    return response
