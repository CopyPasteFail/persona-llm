from __future__ import annotations

import time
import uuid
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .types import ChatRequest, ChatResponse, Usage, Citation
from .settings import settings
from .auth import router as auth_router
from .security import get_current_session
from .retrieval import normalize_question_for_first_person
from .keys import JsonKeyStore, set_key_store

logger = logging.getLogger("api.mock")

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
        yield
        return
    set_key_store(JsonKeyStore(path))
    logger.info("mock key store enabled: %s", path)
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

@app.get("/health")
def health():
    """Return a basic health indicator for the mock service.

    Returns:
        dict: JSON payload containing a static "ok" status.
    """
    return {"status": "ok"}


@app.post("/chat", response_model_exclude_none=True)
def chat(req: ChatRequest, _session=Depends(get_current_session)) -> ChatResponse:
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

    return ChatResponse(
        answer=answer,
        citations=citations,
        usage=usage,
    )
