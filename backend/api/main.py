from __future__ import annotations

import logging
import time
import uuid
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse, Citation, Usage
from .security import verify_api_key, check_rate_limit_dependency
from .settings import settings
from . import retrieval, llm

READY = False
logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

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

@app.on_event("startup")
def on_startup():
    # TODO: in real mode, load side store and clients, then set READY = True
    global READY
    READY = True

@app.get("/health")
def health():
    return {"ready": READY}

@app.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    _auth=Depends(verify_api_key),
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

        # Retrieval pipeline (real mode should implement these):
        emb = retrieval.embed_query(norm_q)
        cands = retrieval.search_vector_store(emb, top_k=8)
        # NEW: no filters passed here
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
                output_tokens=len(answer) // 4,
            )
            return ChatResponse(answer=answer, citations=[], usage=usage)

        # Build prompt and call LLM
        prompt_payload = llm.build_llm_prompt(norm_q, selected)
        _ = llm.call_gemini_flash(prompt_payload, max_output_tokens=settings.MAX_OUTPUT_TOKENS)

        # Until real call is wired, make the failure explicit so we do not silently succeed
        raise NotImplementedError("Real mode not implemented. Use mock backend locally.")

    except NotImplementedError as e:
        logger.info(
            {
                "request_id": request_id,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "ip": getattr(request.client, "host", None),
            }
        )
        raise HTTPException(status_code=503, detail=str(e))
