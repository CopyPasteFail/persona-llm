from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from . import llm
from .prompts import QUESTION_PREFIX
from .settings import settings

DEFAULT_LLM_BACKEND = "vertex"
DETERMINISTIC_LLM_BACKEND = "deterministic"


@runtime_checkable
class LlmBackend(Protocol):
    """Protocol for LLM backends."""

    def generate(
        self, prompt_payload: llm.PromptPayload, max_output_tokens: int
    ) -> tuple[str, llm.UsageMetadata]:
        """Generate a response given a prompt payload."""


class VertexLlmBackend:
    """Gemini Flash backend via Vertex AI."""

    def generate(
        self, prompt_payload: llm.PromptPayload, max_output_tokens: int
    ) -> tuple[str, llm.UsageMetadata]:
        return llm.call_gemini_flash(prompt_payload, max_output_tokens)


class DeterministicLlmBackend:
    """Deterministic LLM backend for mock responses."""

    def generate(
        self, prompt_payload: llm.PromptPayload, max_output_tokens: int
    ) -> tuple[str, llm.UsageMetadata]:
        question = _extract_question(prompt_payload)
        answer = (
            f"TLDR: I am returning a deterministic mock answer for: \"{question}\"\n"
            "- I speak in first person to mirror production behavior.\n"
            "- Grounded in local mock data.\n"
            "Wrap: This is a first-person mock; wire the real LLM to change it."
        )
        usage = {
            "input_tokens": max(1, len(question) // llm.APPROX_CHARS_PER_TOKEN),
            "output_tokens": max(1, len(answer) // llm.APPROX_CHARS_PER_TOKEN),
        }
        return answer, usage


def get_llm_backend(default_backend: str | None = None) -> LlmBackend:
    """Return an LLM backend selected from env or an explicit default."""
    env_value = os.getenv("LLM_BACKEND")
    backend_name = env_value.strip().lower() if env_value else ""
    if not backend_name:
        backend_name = (default_backend or settings.LLM_BACKEND or DEFAULT_LLM_BACKEND).strip().lower()

    if backend_name == DETERMINISTIC_LLM_BACKEND:
        return DeterministicLlmBackend()
    if backend_name == DEFAULT_LLM_BACKEND:
        return VertexLlmBackend()
    raise RuntimeError(f"Unknown LLM_BACKEND: {backend_name}")


def _extract_question(prompt_payload: llm.PromptPayload) -> str:
    """Extract the question line from a prompt payload when present."""
    messages = prompt_payload.get("messages", [])
    for message in messages:
        role = str(message.get("role", "")).lower()
        if role != llm.ROLE_USER:
            continue
        content = str(message.get("content", ""))
        for line in content.splitlines():
            if line.startswith(QUESTION_PREFIX):
                return line[len(QUESTION_PREFIX) :].strip()
    return ""
