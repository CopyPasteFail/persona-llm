from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from . import llm
from .prompts import QUESTION_PREFIX
from .settings import settings

DEFAULT_LLM_BACKEND = "vertex"
DETERMINISTIC_LLM_BACKEND = "deterministic"
ENV_VAR_LLM_BACKEND = "LLM_BACKEND"
DETERMINISTIC_RESPONSE_LINES = (
    'TLDR: I am returning a deterministic mock answer for: "{question}"',
    "- I speak in first person to mirror production behavior.",
    "- Grounded in local mock data.",
    "Wrap: This is a first-person mock; wire the real LLM to change it.",
)


@runtime_checkable
class LlmBackend(Protocol):
    """Protocol for LLM backends.

    Implementations must provide a generate method that accepts a prompt payload
    and returns both the generated text and usage metadata.
    """

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        """Generate a response from the given prompt payload.

        Args:
            prompt_payload: Structured messages and metadata used to build a
                model request.
            max_output_tokens: Upper bound for model output tokens.
            thinking_budget_tokens: Optional per-request override for the model's
                thinking budget.

        Returns:
            A tuple of the generated response string and usage metadata.
        """
        ...


class VertexLlmBackend:
    """Gemini Flash backend via Vertex AI."""

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        """Generate a response using the Gemini Flash model.

        Args:
            prompt_payload: Structured messages and metadata used to build a
                model request.
            max_output_tokens: Upper bound for model output tokens.
            thinking_budget_tokens: Optional per-request override for the model's
                thinking budget.

        Returns:
            A tuple of the generated response string and usage metadata.
        """
        return llm.call_gemini_flash(
            prompt_payload,
            max_output_tokens,
            thinking_budget_tokens=thinking_budget_tokens,
        )


class DeterministicLlmBackend:
    """Deterministic LLM backend for mock responses."""

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        """Generate a deterministic mock response for testing.

        Args:
            prompt_payload: Structured messages and metadata used to build a
                model request.
            max_output_tokens: Upper bound for model output tokens. This is
                unused because the response is fixed and deterministic.
            thinking_budget_tokens: Optional per-request override for the model's
                thinking budget.

        Returns:
            A tuple of the deterministic response string and usage metadata.

        Edge cases:
            - If no question is found in the prompt payload, the response
              references an empty string.
        """
        question = _extract_question(prompt_payload)
        answer = "\n".join(
            line.format(question=question) for line in DETERMINISTIC_RESPONSE_LINES
        )
        usage_metadata: llm.UsageMetadata = {
            "input_tokens": max(1, len(question) // llm.APPROX_CHARS_PER_TOKEN),
            "output_tokens": max(1, len(answer) // llm.APPROX_CHARS_PER_TOKEN),
        }
        return answer, usage_metadata


def get_llm_backend(default_backend: str | None = None) -> LlmBackend:
    """Return an LLM backend selected from environment or defaults.

    Args:
        default_backend: Optional default backend name to use when the
            environment variable and settings are not set.

    Returns:
        The instantiated LLM backend matching the resolved backend name.

    Raises:
        RuntimeError: If the resolved backend name is unknown.
    """
    env_backend_value = os.getenv(ENV_VAR_LLM_BACKEND)
    backend_name = env_backend_value.strip().lower() if env_backend_value else ""
    if not backend_name:
        settings_backend_name = settings.LLM_BACKEND or ""
        backend_name = (
            default_backend or settings_backend_name or DEFAULT_LLM_BACKEND
        ).strip().lower()

    if backend_name == DETERMINISTIC_LLM_BACKEND:
        return DeterministicLlmBackend()
    if backend_name == DEFAULT_LLM_BACKEND:
        return VertexLlmBackend()
    raise RuntimeError(f"Unknown {ENV_VAR_LLM_BACKEND}: {backend_name}")


def _extract_question(prompt_payload: llm.PromptPayload) -> str:
    """Extract a question line from a prompt payload.

    Args:
        prompt_payload: The structured prompt payload containing messages.

    Returns:
        The question string following the configured prefix when present.

    Edge cases:
        - Returns an empty string when no user message contains a question line.
    """
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
