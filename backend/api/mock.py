from __future__ import annotations

import time
import uuid
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse, Usage, Citation
from .auth import router as auth_router
from .security import get_current_session
from .retrieval import normalize_question_for_first_person

app = FastAPI(title="Persona LLM API (mock)", version="0.0.0-mock")

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

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest, _session=Depends(get_current_session)) -> ChatResponse:
    """
    Deterministic mock that:
      - Accepts only { question } (extra fields ignored by pydantic Config).
      - Normalizes third-person mentions of Omer to first person.
      - Always returns a fixed-structure response with a dummy citation.
    """
    _request_id = str(uuid.uuid4())
    _t0 = time.time()

    question = (req.question or "").strip()
    norm_q = normalize_question_for_first_person(question)

    answer = (
        f"TLDR: I am returning a deterministic mock answer for: \"{norm_q}\"\n"
        f"- I speak in first person to mirror production behavior.\n"
        f"- Grounded in local mock data.\n"
        f"Wrap: This is a first-person mock; wire the real LLM to change it."
    )

    usage = Usage(
        input_tokens=max(1, len(norm_q) // 4),
        output_tokens=len(answer) // 4,
    )

    citations = [Citation(id="mock:1", text="deterministic mock chunk")]

    return ChatResponse(answer=answer, citations=citations, usage=usage)
