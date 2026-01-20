from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse
from .security import Session, check_rate_limit_dependency, get_current_session
from .settings import settings
from .auth import router as auth_router
from . import dataset_cache, retrieval
from . import llm_backends, ops_routes, rag_chat_orchestrator

API_TITLE = "Persona LLM API"
API_VERSION = "1.0.0"
ALLOWED_HEADERS = ["*"]
ALLOWED_METHODS = ["GET", "POST"]
EVENT_CHAT_FAILED = "chat_failed"
EVENT_CHAT_NO_SIGNAL = "chat.no_signal"
EVENT_CHAT_SUCCESS = "chat.success"
CHAT_UNAVAILABLE_DETAIL = "chat_unavailable"
HEALTH_STATUS_OK = "ok"
LOCALHOST_ORIGIN = "http://localhost:3000"
NOT_READY_DETAIL = "not ready"
PLACEHOLDER_ORIGIN = "https://placeholder.web.app"
SEARCH_TOP_K = 8
SERVICE_UNAVAILABLE_STATUS = 503

is_ready = False
is_init_done = False
logger = logging.getLogger(__name__)
_log_level_name = os.getenv("APP_LOG_LEVEL", "INFO").strip().upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(level=_log_level)

_llm_backend = llm_backends.get_llm_backend(default_backend="vertex")

@asynccontextmanager
async def lifespan(_app_instance: FastAPI) -> AsyncIterator[None]:
    """Initialize the API's dataset cache and readiness flags.

    Inputs:
        _app_instance: The FastAPI application instance.

    Outputs:
        None. Updates process-level readiness state.

    Edge cases:
        Startup failures leave the API unready; errors are logged.
    """
    global is_ready, is_init_done
    try:
        cache = dataset_cache.reload_cache()
        retrieval.configure_chunk_store(cache.chunks_by_id)
        is_ready = bool(cache.chunks_by_id)
        if not is_ready:
            logger.warning("Chunk store loaded but empty; API remains unready.")
    except Exception:
        is_ready = False
        logger.exception("Failed to warm chunk store during startup.")
    finally:
        is_init_done = True
    yield


app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)

origins = [
    LOCALHOST_ORIGIN,
    f"https://{settings.PROJECT_ID}.web.app"
    if settings.PROJECT_ID
    else PLACEHOLDER_ORIGIN,
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=ALLOWED_METHODS,
    allow_headers=ALLOWED_HEADERS,
)

app.include_router(auth_router)
app.include_router(ops_routes.router)

@app.get("/health")
def health():
    """Return a basic health signal for liveness checks.
    """
    return {"status": HEALTH_STATUS_OK}


@app.get("/ready")
def ready():
    """Return readiness state for dependent services.

    Inputs:
        None.

    Outputs:
        JSON indicating readiness.

    Edge cases:
        Returns HTTP 503 until startup initialization completes.
    """
    if not is_init_done:
        raise HTTPException(
            status_code=SERVICE_UNAVAILABLE_STATUS,
            detail=NOT_READY_DETAIL,
        )
    return {"ready": True}


@app.post("/chat")
async def chat(
    chat_request: ChatRequest,
    request: Request,
    session: Session = Depends(get_current_session),
    _rate_limit: None = Depends(check_rate_limit_dependency),
) -> ChatResponse:
    """Answer a chat question using retrieval-augmented generation.

    Inputs:
        chat_request: User question and metadata payload.
        request: FastAPI request with client info.
        session: Current authenticated session.
        _rate_limit: Rate-limit dependency result.

    Outputs:
        ChatResponse with answer text, citations, and token usage.

    Edge cases:
        Returns HTTP 503 when the API is not ready or on provider failures.
    """
    if not is_ready:
        raise HTTPException(
            status_code=SERVICE_UNAVAILABLE_STATUS,
            detail=NOT_READY_DETAIL,
        )

    request_id = str(uuid.uuid4())
    request_start_time = time.time()

    try:
        question = chat_request.question
        chat_result = rag_chat_orchestrator.run_rag_chat(
            question,
            retrieval=retrieval,
            llm_backend=_llm_backend,
            top_k=SEARCH_TOP_K,
            persona_name=settings.PERSONA_NAME,
            max_input_tokens=settings.MAX_INPUT_TOKENS,
            max_output_tokens=settings.MAX_OUTPUT_TOKENS,
        )
        response = chat_result.response

        if not chat_result.selected_chunks:
            logger.debug(
                {
                    "event": EVENT_CHAT_NO_SIGNAL,
                    "request_id": request_id,
                    "elapsed_ms": int((time.time() - request_start_time) * 1000),
                    "ip": getattr(request.client, "host", None),
                    "key_id": getattr(session, "key_id", None),
                }
            )
            return response

        logger.info(
            {
                "event": EVENT_CHAT_SUCCESS,
                "request_id": request_id,
                "elapsed_ms": int((time.time() - request_start_time) * 1000),
                "ip": getattr(request.client, "host", None),
                "chunks": [citation.id for citation in response.citations],
                "usage": response.usage.model_dump(),
                "key_id": session.key_id,
            }
        )

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            EVENT_CHAT_FAILED,
            extra={
                "request_id": request_id,
                "elapsed_ms": int((time.time() - request_start_time) * 1000),
                "ip": getattr(request.client, "host", None),
                "key_id": getattr(session, "key_id", None),
            },
        )
        raise HTTPException(
            status_code=SERVICE_UNAVAILABLE_STATUS,
            detail=CHAT_UNAVAILABLE_DETAIL,
        ) from exc
