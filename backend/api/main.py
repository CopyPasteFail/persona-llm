from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse, Citation, Usage
from .security import Session, check_rate_limit_dependency, get_current_session
from .settings import settings
from .auth import router as auth_router
from . import retrieval, llm

API_TITLE = "Persona LLM API"
API_VERSION = "1.0.0"
APPROX_CHARS_PER_TOKEN = 4
ALLOWED_HEADERS = ["*"]
ALLOWED_METHODS = ["GET", "POST"]
EVENT_CHAT_FAILED = "chat_failed"
EVENT_CHAT_NO_SIGNAL = "chat.no_signal"
EVENT_CHAT_SUCCESS = "chat.success"
CHAT_UNAVAILABLE_DETAIL = "chat_unavailable"
HEALTH_STATUS_OK = "ok"
LOCALHOST_ORIGIN = "http://localhost:3000"
NOT_READY_DETAIL = "not ready"
NO_SIGNAL_ANSWER = (
    "TLDR: I do not have that in my indexed experience right now.\n"
    "- I only summarize what is in my available context.\n"
    "- Ask again with a more specific query.\n"
    "- Or tell me to expand the data source.\n"
    "Wrap: Ask me something that appears in my experience or projects."
)
PLACEHOLDER_ORIGIN = "https://placeholder.web.app"
SEARCH_TOP_K = 8
SERVICE_UNAVAILABLE_STATUS = 503
UNABLE_TO_GENERATE_ANSWER = "TLDR: Unable to generate an answer.\nWrap: Try again shortly."

is_ready = False
is_init_done = False
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_SNIPPET_CHAR_LIMIT = 320

@asynccontextmanager
async def lifespan(_app_instance: FastAPI) -> AsyncIterator[None]:
    """Initialize the API's chunk store and readiness flags.

    Inputs:
        _app_instance: The FastAPI application instance.

    Outputs:
        None. Updates process-level readiness state.

    Edge cases:
        Startup failures leave the API unready; errors are logged.
    """
    global is_ready, is_init_done
    try:
        is_ready = retrieval.warm_chunk_store()
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
        # Normalize to speak as "I" even if the question mentions "Omer"
        question = (chat_request.question or "").strip()
        normalized_question = retrieval.normalize_question_for_first_person(question)

        query_embedding = retrieval.embed_query(normalized_question)
        candidate_chunks = retrieval.search_vector_store(
            query_embedding,
            top_k=SEARCH_TOP_K,
        )
        selected_chunks = retrieval.apply_filters_and_boosting(candidate_chunks)

        # Out-of-scope honesty: if no usable chunks, do not hallucinate
        if not retrieval.has_signal(selected_chunks):
            answer = NO_SIGNAL_ANSWER
            usage = Usage(
                input_tokens=max(1, len(normalized_question) // APPROX_CHARS_PER_TOKEN),
                output_tokens=max(1, len(answer) // APPROX_CHARS_PER_TOKEN),
            )
            logger.debug(
                {
                    "event": EVENT_CHAT_NO_SIGNAL,
                    "request_id": request_id,
                    "elapsed_ms": int((time.time() - request_start_time) * 1000),
                    "ip": getattr(request.client, "host", None),
                    "key_id": getattr(session, "key_id", None),
                }
            )
            return ChatResponse(
                answer=answer,
                citations=[],
                usage=usage,
                input_token_limit=settings.MAX_INPUT_TOKENS,
            )

        prompt_payload = llm.build_llm_prompt(
            normalized_question,
            selected_chunks,
            persona_name=settings.PERSONA_NAME,
            max_input_tokens=settings.MAX_INPUT_TOKENS,
        )
        answer_text, usage_meta = llm.call_gemini_flash(
            prompt_payload,
            max_output_tokens=settings.MAX_OUTPUT_TOKENS,
        )
        answer_final = answer_text.strip() or UNABLE_TO_GENERATE_ANSWER

        citations = [_chunk_to_citation(chunk) for chunk in selected_chunks]
        usage = _usage_from_llm_meta(
            usage_meta,
            question=normalized_question,
            answer=answer_final,
        )

        logger.info(
            {
                "event": EVENT_CHAT_SUCCESS,
                "request_id": request_id,
                "elapsed_ms": int((time.time() - request_start_time) * 1000),
                "ip": getattr(request.client, "host", None),
                "chunks": [citation.id for citation in citations],
                "usage": usage.model_dump(),
                "key_id": session.key_id,
            }
        )

        return ChatResponse(
            answer=answer_final,
            citations=citations,
            usage=usage,
            input_token_limit=settings.MAX_INPUT_TOKENS,
        )

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


def _usage_from_llm_meta(meta: Dict[str, int], *, question: str, answer: str) -> Usage:
    """Build usage metrics from LLM metadata or fallback estimation.

    Inputs:
        meta: LLM metadata containing optional token counts.
        question: Normalized question string.
        answer: Final answer string.

    Outputs:
        Usage with input and output token counts.

    Edge cases:
        Falls back to approximate character-based estimation when counts are
        missing or non-positive.
    """
    fallback_input = max(1, len(question) // APPROX_CHARS_PER_TOKEN)
    fallback_output = max(1, len(answer) // APPROX_CHARS_PER_TOKEN)
    input_tokens = int(meta.get("input_tokens", fallback_input))
    output_tokens = int(meta.get("output_tokens", fallback_output))
    if input_tokens <= 0:
        input_tokens = fallback_input
    if output_tokens <= 0:
        output_tokens = fallback_output
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def _chunk_to_citation(chunk: Dict[str, Any]) -> Citation:
    """Convert a retrieval chunk into a response citation.

    Inputs:
        chunk: Raw chunk with optional id and text fields.

    Outputs:
        Citation with an id and a normalized snippet.

    Edge cases:
        Snippets are whitespace-normalized and truncated to the configured
        character limit.
    """
    chunk_id = str(chunk.get("id") or "")
    text = str(chunk.get("text") or "").strip()
    snippet = " ".join(text.split())
    if snippet and len(snippet) > _SNIPPET_CHAR_LIMIT:
        snippet = snippet[: _SNIPPET_CHAR_LIMIT - 3].rstrip() + "..."
    return Citation(id=chunk_id, text=snippet or None)
