from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse, Citation, Usage
from .security import get_current_session, check_rate_limit_dependency
from .settings import settings
from .auth import router as auth_router
from . import retrieval, llm

READY = False
INIT_DONE = False
logger = logging.getLogger(" api")
logging.basicConfig(level=logging.INFO)

_SNIPPET_CHAR_LIMIT = 320

app = FastAPI(title="Persona LLM API", version="1.0.0")

origins = [
    "http://localhost:3000",
    f"https://{settings.PROJECT_ID}.web.app" if settings.PROJECT_ID else "https://placeholder.web.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(auth_router)

@app.on_event("startup")
def on_startup():
    global READY, INIT_DONE
    try:
        READY = retrieval.warm_chunk_store()
        if not READY:
            logger.warning("Chunk store loaded but empty; API remains unready.")
    except Exception:
        READY = False
        logger.exception("Failed to warm chunk store during startup.")
    finally:
        INIT_DONE = True

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/ready")
def ready():
    if not INIT_DONE:
        raise HTTPException(status_code=503, detail="not ready")
    return {"ready": True}

@app.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    session=Depends(get_current_session),
    _rl=Depends(check_rate_limit_dependency),
) -> ChatResponse:
    if not READY:
        raise HTTPException(status_code=503, detail="not ready")

    request_id = str(uuid.uuid4())
    t0 = time.time()

    try:
        # Normalize to speak as "I" even if the question mentions "Omer"
        question = (req.question or "").strip()
        norm_q = retrieval.normalize_question_for_first_person(question)

        emb = retrieval.embed_query(norm_q)
        cands = retrieval.search_vector_store(emb, top_k=8)
        selected = retrieval.apply_filters_and_boosting(cands)

        # Out-of-scope honesty: if no usable chunks, do not hallucinate
        if not retrieval.has_signal(selected):
            answer = (
                "TLDR: I do not have that in my indexed experience right now.\n"
                "- I only summarize what is in my available context.\n"
                "- Ask again with a more specific query.\n"
                "- Or tell me to expand the data source.\n"
                "Wrap: Ask me something that appears in my experience or projects."
            )
            usage = Usage(
                input_tokens=max(1, len(norm_q) // 4),
                output_tokens=max(1, len(answer) // 4),
            )
            logger.debug(
                {
                    " event": "chat.no_signal",
                    "request_id": request_id,
                    "elapsed_ms": int((time.time() - t0) * 1000),
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
            norm_q,
            selected,
            max_input_tokens=settings.MAX_INPUT_TOKENS,
        )
        answer_text, usage_meta = llm.call_gemini_flash(
            prompt_payload,
            max_output_tokens=settings.MAX_OUTPUT_TOKENS,
        )
        answer_final = answer_text.strip() or "TLDR: Unable to generate an answer.\nWrap: Try again shortly."

        citations = [_chunk_to_citation(chunk) for chunk in selected]
        usage = _usage_from_llm_meta(
            usage_meta,
            question=norm_q,
            answer=answer_final,
        )

        logger.info(
            {
                "event": "chat.success",
                "request_id": request_id,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "ip": getattr(request.client, "host", None),
                "chunks": [c.id for c in citations],
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
            "chat_failed",
            extra={
                "request_id": request_id,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "ip": getattr(request.client, "host", None),
                "key_id": getattr(session, "key_id", None),
            },
        )
        raise HTTPException(status_code=503, detail="chat_unavailable") from exc


def _usage_from_llm_meta(meta: Dict[str, int], *, question: str, answer: str) -> Usage:
    fallback_input = max(1, len(question) // 4)
    fallback_output = max(1, len(answer) // 4)
    input_tokens = int(meta.get("input_tokens", fallback_input))
    output_tokens = int(meta.get("output_tokens", fallback_output))
    if input_tokens <= 0:
        input_tokens = fallback_input
    if output_tokens <= 0:
        output_tokens = fallback_output
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def _chunk_to_citation(chunk: Dict[str, Any]) -> Citation:
    chunk_id = str(chunk.get("id") or "")
    text = str(chunk.get("text") or "").strip()
    snippet = " ".join(text.split())
    if snippet and len(snippet) > _SNIPPET_CHAR_LIMIT:
        snippet = snippet[: _SNIPPET_CHAR_LIMIT - 3].rstrip() + "..."
    return Citation(id=chunk_id, text=snippet or None)
