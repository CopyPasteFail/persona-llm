from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse, Usage
from .security import Session, check_rate_limit_dependency, get_current_session
from .settings import settings
from .auth import router as auth_router
from . import dataset_cache, retrieval
from . import llm_backends, ops_routes, rag_chat_orchestrator
from .llm import GeminiEmptyResponseError

API_TITLE = "Persona LLM API"
API_VERSION = "1.0.0"
ALLOWED_HEADERS = ["*"]
ALLOWED_METHODS = ["GET", "POST"]
EVENT_CHAT_FAILED = "chat_failed"
EVENT_CHAT_NO_SIGNAL = "chat.no_signal"
EVENT_CHAT_ANSWER_PREVIEW = "chat.answer_preview"
EVENT_CHAT_SUCCESS = "chat.success"
CHAT_UNAVAILABLE_DETAIL = "chat_unavailable"
HEALTH_STATUS_OK = "ok"
LOCALHOST_ORIGIN = "http://localhost:3000"
NOT_READY_DETAIL = "not ready"
PLACEHOLDER_ORIGIN = "https://placeholder.web.app"
SEARCH_TOP_K = 4
SERVICE_UNAVAILABLE_STATUS = 503
ANSWER_PREVIEW_HEAD_CHARS = 200
ANSWER_PREVIEW_TAIL_CHARS = 200
EMPTY_ANSWER_CLASS_TOKEN_STARVATION = "token_starvation"
EMPTY_ANSWER_CLASS_NO_RELEVANT_CONTEXT = "no_relevant_context"
EMPTY_ANSWER_CLASS_UNKNOWN = "empty_text_unknown"
TOKEN_STARVATION_MESSAGE = (
    "I couldn\u2019t finish the reply. Try again, or ask for a shorter answer."
)

is_ready = False
is_init_done = False
logger = logging.getLogger(__name__)
_log_level_name = os.getenv("APP_LOG_LEVEL", "INFO").strip().upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(level=_log_level)

_llm_backend = llm_backends.get_llm_backend(default_backend="vertex")


def _log_chat_answer_preview(answer_text: str, request_id: str, *, context: str) -> None:
    """Log answer length and a bounded preview before returning a ChatResponse.

    Inputs: answer text, request id, and a context label for the caller.
    Outputs: None; emits a debug log when enabled.
    Edge cases: empty answers emit zero-length previews.
    Concurrency: stateless; safe for concurrent calls.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    answer_length = len(answer_text)
    head_preview = answer_text[:ANSWER_PREVIEW_HEAD_CHARS]
    should_skip_tail = (
        answer_length <= ANSWER_PREVIEW_HEAD_CHARS + ANSWER_PREVIEW_TAIL_CHARS
    )
    tail_preview = ""
    if answer_text and not should_skip_tail:
        tail_preview = answer_text[-ANSWER_PREVIEW_TAIL_CHARS:]
    logger.debug(
        {
            "event": EVENT_CHAT_ANSWER_PREVIEW,
            "request_id": request_id,
            "context": context,
            "answer_length": answer_length,
            "answer_head": head_preview,
            "answer_tail": tail_preview,
        }
    )

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
            enable_thinking_gating=settings.ENABLE_THINKING_GATING,
            default_thinking_budget_tokens=settings.THINKING_BUDGET_TOKENS,
            enable_signal_gating=settings.ENABLE_SIGNAL_GATING,
            weighted_score_threshold=settings.WEIGHTED_SCORE_THRESHOLD,
            bm25_threshold=settings.BM25_THRESHOLD,
        )
        response = chat_result.response
        response.model = settings.LLM_MODEL_NAME

        if not chat_result.selected_chunks:
            _log_chat_answer_preview(
                response.answer,
                request_id,
                context=EVENT_CHAT_NO_SIGNAL,
            )
            logger.debug(
                {
                    "event": EVENT_CHAT_NO_SIGNAL,
                    "request_id": request_id,
                    "elapsed_ms": int((time.time() - request_start_time) * 1000),
                    "ip": getattr(request.client, "host", None),
                    "key_id": getattr(session, "key_id", None),
                    "thinking_budget_tokens_effective": chat_result.thinking_budget_tokens_effective,
                    "thinking_gating_enabled": settings.ENABLE_THINKING_GATING,
                    "signal_gate_enabled": settings.ENABLE_SIGNAL_GATING,
                    "signal_would_skip_llm": chat_result.signal_would_skip_llm,
                    "signal_gate_reason": chat_result.signal_gate_reason,
                    # Canonical top-1 retrieval metrics; signal_top1_* aliases removed.
                    "top1_weighted_score": chat_result.top1_weighted_score,
                    "top1_bm25_score": chat_result.top1_bm25_score,
                    "top1_vector_score": chat_result.top1_vector_score,
                    "weighted_score_threshold": chat_result.weighted_score_threshold,
                    "bm25_threshold": chat_result.bm25_threshold,
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
                "usage_detail": chat_result.usage_detail,
                "llm_limits": {
                    "max_output_tokens": settings.MAX_OUTPUT_TOKENS,
                    "thinking_budget_tokens": settings.THINKING_BUDGET_TOKENS,
                },
                "thinking_budget_tokens_effective": chat_result.thinking_budget_tokens_effective,
                "thinking_gating_enabled": settings.ENABLE_THINKING_GATING,
                "signal_gate_enabled": settings.ENABLE_SIGNAL_GATING,
                "signal_would_skip_llm": chat_result.signal_would_skip_llm,
                "signal_gate_reason": chat_result.signal_gate_reason,
                # Canonical top-1 retrieval metrics; signal_top1_* aliases removed.
                "top1_weighted_score": chat_result.top1_weighted_score,
                "top1_bm25_score": chat_result.top1_bm25_score,
                "top1_vector_score": chat_result.top1_vector_score,
                "weighted_score_threshold": chat_result.weighted_score_threshold,
                "bm25_threshold": chat_result.bm25_threshold,
                "key_id": session.key_id,
            }
        )

        _log_chat_answer_preview(
            response.answer,
            request_id,
            context=EVENT_CHAT_SUCCESS,
        )
        return response

    except HTTPException:
        raise
    except GeminiEmptyResponseError as exc:
        empty_answer_classification = (
            EMPTY_ANSWER_CLASS_TOKEN_STARVATION
            if exc.is_token_starvation
            else EMPTY_ANSWER_CLASS_UNKNOWN
        )
        # TODO: Detect EMPTY_ANSWER_CLASS_NO_RELEVANT_CONTEXT when RAG yields no usable context.
        if exc.is_token_starvation:
            message = (
                "Gemini returned no text, likely token starvation "
                "(thinking consumed output budget)"
            )
        else:
            message = "Gemini returned no text, cause unknown"
        logger.warning(
            message,
            extra={
                "request_id": request_id,
                "elapsed_ms": int((time.time() - request_start_time) * 1000),
                "ip": getattr(request.client, "host", None),
                "finish_reason": exc.finish_reason,
                "prompt_token_count": exc.prompt_token_count,
                "total_token_count": exc.total_token_count,
                "thoughts_token_count": exc.thoughts_token_count,
                "is_token_starvation": exc.is_token_starvation,
                "key_id": getattr(session, "key_id", None),
            },
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                {
                    "event": "chat.empty_text_usage",
                    "request_id": request_id,
                    "finish_reason": exc.finish_reason,
                    "prompt_token_count": exc.prompt_token_count,
                    "total_token_count": exc.total_token_count,
                    "thoughts_token_count": exc.thoughts_token_count,
                    "max_output_tokens": settings.MAX_OUTPUT_TOKENS,
                }
            )
        return ChatResponse(
            answer=TOKEN_STARVATION_MESSAGE
            if empty_answer_classification == EMPTY_ANSWER_CLASS_TOKEN_STARVATION
            else "",
            citations=[],
            usage=Usage(input_tokens=0, output_tokens=0, thoughts_tokens=None),
            input_token_limit=settings.MAX_INPUT_TOKENS,
            model=settings.LLM_MODEL_NAME,
        )
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
