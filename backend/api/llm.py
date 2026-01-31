from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Optional, Protocol, TypedDict, cast, runtime_checkable

from .prompts import (
    CONTEXT_HEADER,
    CONTEXT_ONLY_INSTRUCTION,
    PROMPT_OUTPUT_FORMAT,
    QUESTION_PREFIX,
    SYSTEM_PROMPT_TEMPLATE,
)
from .settings import settings

APPROX_CHARS_PER_TOKEN = 4
MIN_ESTIMATED_TOKENS = 1
DEFAULT_MODEL_NAME = "gemini-2.5-flash"
FALLBACK_MODEL_NAME = "gemini-1.5-flash"
DEFAULT_GENERATION_TEMPERATURE = 0.2
DEFAULT_GENERATION_TOP_P = 0.9
ROLE_SYSTEM = "system"
ROLE_USER = "user"
PROMPT_MESSAGES_KEY = "messages"
PROMPT_ROLE_KEY = "role"
PROMPT_CONTENT_KEY = "content"
USAGE_PROMPT_TOKEN_KEYS = ("prompt_token_count", "prompt_tokens")
USAGE_CANDIDATE_TOKEN_KEYS = (
    "candidates_token_count",
    "candidates_tokens",
    "response_token_count",
    "response_tokens",
)
USAGE_TOTAL_TOKEN_KEYS = ("total_token_count", "total_tokens")
USAGE_THOUGHTS_TOKEN_KEYS = ("thoughts_token_count", "thoughts_tokens")
FINISH_REASON_MAX_TOKENS = "MAX_TOKENS"
TOKEN_STARVATION_THRESHOLD_FRACTION = 0.85

Chunk = dict[str, Any]
UsageMetadata = dict[str, int | str]

logger = logging.getLogger(__name__)


class GeminiEmptyResponseError(RuntimeError):
    """
    Error raised when Gemini returns a response without extractable text.

    Inputs: message and optional metadata extracted from the response.
    Output: an exception carrying finish reason, token counts, and a token-starvation flag.
    Edge cases: metadata fields are None when usage or finish info is unavailable.
    """

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str | None,
        prompt_token_count: int | None,
        total_token_count: int | None,
        thoughts_token_count: int | None,
        is_token_starvation: bool,
    ) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.prompt_token_count = prompt_token_count
        self.total_token_count = total_token_count
        self.thoughts_token_count = thoughts_token_count
        self.is_token_starvation = is_token_starvation


class PromptMessage(TypedDict):
    role: str
    content: str


class PromptPayloadDict(TypedDict):
    messages: list[PromptMessage]


PromptPayload = PromptPayloadDict


def _estimate_tokens(text: str) -> int:
    """
    Estimate token count using a chars-per-token heuristic.

    Input: raw text string.
    Output: an estimated token count as a positive int,
    or zero when the input is empty.
    Edge cases: returns at least
    MIN_ESTIMATED_TOKENS for non-empty strings to avoid undercounting.
    """
    if not text:
        return 0
    return max(MIN_ESTIMATED_TOKENS, len(text) // APPROX_CHARS_PER_TOKEN)


def _llm_debug_enabled() -> bool:
    value = os.getenv("LLM_DEBUG", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _build_user_prompt(question: str, context_block: str) -> str:
    """
    Build the user prompt with the question and context block.

    Inputs: a question string and a preformatted context block. Output: a single prompt string containing the question, context header, and the strict
    context-only instruction.
    Edge cases: when context_block is empty, the
    section is left blank to keep the prompt structure consistent.
    """
    question_line = f"{QUESTION_PREFIX}{question}"
    if context_block:
        return (
            f"{question_line}\n\n"
            f"{CONTEXT_HEADER}\n{context_block}\n\n"
            f"{CONTEXT_ONLY_INSTRUCTION}"
        )
    return (
        f"{question_line}\n\n"
        f"{CONTEXT_HEADER}\n\n"
        f"{CONTEXT_ONLY_INSTRUCTION}"
    )


def _get_prompt_messages(payload: PromptPayload) -> list[PromptMessage]:
    """
    Extract prompt messages from a payload.

    Input: a prompt payload dict. Output: list of messages with role/content keys.
    Edge cases: relies on prompt payload structure being valid and raises KeyError if the messages key is missing.
    """
    return payload[PROMPT_MESSAGES_KEY]


def _trim_chunks_to_budget(
    chunks: list[Chunk],
    *,
    max_input_tokens: Optional[int],
    system_prompt: str,
    question: str,
) -> list[Chunk]:
    """
    Trim context chunks to fit within the input token budget.

    Inputs: chunks with text fields, token budget, system prompt, and question.
    Output: a list of chunks capped to fit the estimated budget. Edge cases: when the budget is too small, returns the first chunk or a truncated first
    chunk; when the budget is invalid, returns the original chunk list.
    """
    if not max_input_tokens or max_input_tokens <= 0:
        return chunks

    base_user = _build_user_prompt(question, "")
    base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(base_user)
    remaining = max_input_tokens - base_tokens
    if remaining <= 0:
        return chunks[:1]

    trimmed: list[Chunk] = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        text = str(chunk.get("text", ""))
        block = f"[{chunk_index}] {text}"
        block_tokens = _estimate_tokens(block)
        if block_tokens <= remaining:
            trimmed.append(chunk)
            remaining -= block_tokens
            continue

        if not trimmed:
            max_chars = max(1, remaining * APPROX_CHARS_PER_TOKEN)
            truncated = text[:max_chars].rstrip()
            if truncated:
                trimmed.append(
                    {
                        "id": chunk.get("id"),
                        "text": truncated,
                        "metadata": chunk.get("metadata"),
                    }
                )
        break

    return trimmed or chunks[:1]


def build_llm_prompt(
    question: str,
    chunks: list[Chunk],
    *,
    persona_name: str,
    max_input_tokens: Optional[int] = None,
) -> PromptPayload:
    """
    Build a system and user prompt that instructs the model to answer as a persona.

    Inputs: user question, retrieved context chunks, persona name, and an optional input token budget.
    Output: a prompt payload with system and user messages suitable for chat-style LLM APIs.
    Edge cases: if the budget is too
    small, only the first chunk or its truncated text is included.
    """
    system = SYSTEM_PROMPT_TEMPLATE.format(
        persona_name=persona_name,
        output_format=PROMPT_OUTPUT_FORMAT,
    )

    selected = _trim_chunks_to_budget(
        chunks,
        max_input_tokens=max_input_tokens,
        system_prompt=system,
        question=question,
    )

    context_lines: list[str] = []
    for chunk_index, chunk in enumerate(selected, start=1):
        chunk_text = str(chunk.get("text", ""))
        context_lines.append(f"[{chunk_index}] {chunk_text}")
    context_block = "\n\n".join(context_lines)
    user_prompt = _build_user_prompt(question, context_block)

    # Return a generic chat payload (adapt in your client if needed).
    payload: PromptPayload = {
        "messages": [
            {"role": ROLE_SYSTEM, "content": system},
            {"role": ROLE_USER, "content": user_prompt},
        ]
    }
    return payload


@runtime_checkable
class _GeminiClient(Protocol):
    def generate(
        self,
        *,
        system_prompt: str,
        user_messages: Sequence[str],
        max_output_tokens: int,
    ) -> tuple[str, UsageMetadata]:
        ...


_llm_client: Optional[_GeminiClient] = None


def configure_llm_client(client: Optional[_GeminiClient]) -> None:
    """
    Configure the module-level LLM client.

    Input: a client instance or None to reset.
    Output: none.
    Edge cases: passing None triggers lazy re-creation on the next use.
    """
    global _llm_client
    _llm_client = client


def _get_llm_client() -> _GeminiClient:
    """
    Return a cached LLM client, creating one if needed.

    Output: a Gemini client ready for generate calls.
    Edge cases: if no client is configured, a default google-genai Gemini client is created.
    Concurrency: this cache is not protected by a lock, so concurrent first access may create
    more than one client, but the module variable is updated once set.
    """
    global _llm_client
    if _llm_client is None:
        model_name = (settings.LLM_MODEL_NAME or DEFAULT_MODEL_NAME).strip()
        _llm_client = _GeminiGenaiClient(
            project=settings.PROJECT_ID,
            region=settings.REGION,
            model_name=model_name,
            thinking_budget_tokens=settings.THINKING_BUDGET_TOKENS,
        )
    return _llm_client


def call_gemini_flash(payload: PromptPayload, max_output_tokens: int) -> tuple[str, UsageMetadata]:
    """
    Call Gemini via the google-genai SDK (Vertex mode).

    Inputs: prompt payload and max output tokens.
    Output: answer text plus usage
    metadata if provided by the API. Edge cases: missing user content raises a
    runtime error; empty system content is allowed and omitted.
    """
    messages = _get_prompt_messages(payload)
    system_parts: list[str] = []
    user_messages: list[str] = []

    for message in messages:
        role = str(message.get(PROMPT_ROLE_KEY) or "").lower()
        content = str(message.get(PROMPT_CONTENT_KEY) or "").strip()
        if not content:
            continue
        if role == ROLE_SYSTEM:
            system_parts.append(content)
        else:
            user_messages.append(content)

    if not user_messages:
        raise RuntimeError("LLM payload is missing user content")

    client = _get_llm_client()
    system_prompt = "\n\n".join(system_parts)
    return client.generate(
        system_prompt=system_prompt,
        user_messages=user_messages,
        max_output_tokens=max_output_tokens,
    )


class _GeminiGenaiClient:
    """
    Gemini client using the `google-genai` SDK (Vertex mode) for generation.

    This client replaces the deprecated `vertexai.generative_models` Gemini API.
    It supports optional thinking-budget tuning for Gemini 2.5 models.
    """

    def __init__(
        self,
        *,
        project: str,
        region: str,
        model_name: str,
        thinking_budget_tokens: int | None,
    ) -> None:
        """
        Initialize a google-genai Gemini client.

        Inputs:
            project: GCP project id.
            region: Vertex region (location).
            model_name: Model name (e.g. gemini-2.5-flash).
            thinking_budget_tokens: Optional token budget for the model's thinking.

        Output: None. Client construction is lazy and synchronized.
        Edge cases: a zero budget is allowed and can mean "no thinking" when supported.
        """
        self._project = project
        self._region = region
        self._model_name = model_name
        self._thinking_budget_tokens = (
            int(thinking_budget_tokens) if thinking_budget_tokens is not None else None
        )
        self._client: Any | None = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> Any:
        """
        Create the google-genai client exactly once.

        Output: initialized genai.Client instance.
        Edge cases: concurrent callers synchronize via a lock.
        """
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is not None:
                return self._client
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types  # type: ignore[import-not-found]

            http_options = types.HttpOptions(timeout=settings.REQ_TIMEOUT_MS)
            self._client = genai.Client(
                vertexai=True,
                project=self._project,
                location=self._region,
                http_options=http_options,
            )
            return self._client

    def generate(
        self,
        *,
        system_prompt: str,
        user_messages: Sequence[str],
        max_output_tokens: int,
    ) -> tuple[str, UsageMetadata]:
        """
        Generate a response using google-genai in Vertex mode.

        Inputs: system prompt, user message list, and output token limit.
        Output: extracted response text and usage metadata.
        Edge cases: empty user messages raise; empty extracted text raises
        GeminiEmptyResponseError with finish/usage metadata when available.
        """
        if not user_messages:
            raise RuntimeError("Gemini client received no user messages")
        joined_user_text_pieces: list[str] = []
        for message_text in user_messages:
            cleaned = message_text.strip() if message_text else ""
            if cleaned:
                joined_user_text_pieces.append(cleaned)
        joined_user_text = "\n\n".join(joined_user_text_pieces)
        if not joined_user_text:
            raise RuntimeError("Gemini client received only empty user messages")

        from google.genai import types  # type: ignore[import-not-found]
        from google.api_core import exceptions as google_exceptions

        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            max_output_tokens=max_output_tokens,
            temperature=DEFAULT_GENERATION_TEMPERATURE,
            top_p=DEFAULT_GENERATION_TOP_P,
        )
        if self._thinking_budget_tokens is not None:
            config.thinking_config = types.ThinkingConfig(
                include_thoughts=settings.INCLUDE_THOUGHTS,
                thinking_budget=self._thinking_budget_tokens,
            )

        if _llm_debug_enabled():
            logger.debug(
                {
                    "event": "llm_prompt_debug",
                    "sdk": "google-genai",
                    "model": self._model_name,
                    "system_prompt": system_prompt,
                    "user_messages": list(user_messages),
                    "max_output_tokens": max_output_tokens,
                    "thinking_budget_tokens": self._thinking_budget_tokens,
                }
            )

        client = self._ensure_client()
        try:
            response = client.models.generate_content(
                model=self._model_name,
                contents=[
                    types.Content(
                        role=ROLE_USER,
                        parts=[types.Part.from_text(text=joined_user_text)],
                    )
                ],
                config=config,
            )
        except google_exceptions.NotFound:
            if self._model_name == FALLBACK_MODEL_NAME:
                raise
            response = client.models.generate_content(
                model=FALLBACK_MODEL_NAME,
                contents=[
                    types.Content(
                        role=ROLE_USER,
                        parts=[types.Part.from_text(text=joined_user_text)],
                    )
                ],
                config=config,
            )

        extracted_text = _extract_response_text(response, max_output_tokens)
        usage_metadata = _extract_usage(response)
        return extracted_text, usage_metadata


def _extract_response_text(response: Any, max_output_tokens: int | None = None) -> str:
    """
    Extract response text from a Gemini API response.

    Input: raw SDK response object or dict.
    Output: aggregated response text.
    Edge cases: if no text is found, raises GeminiEmptyResponseError; trims whitespace
    and joins candidate parts with newlines.
    """
    try:
        text = response.text
    except (AttributeError, ValueError, TypeError) as exc:
        safety_info = _format_safety_info(response, max_output_tokens=max_output_tokens)
        message = "Gemini response contained no text"
        if safety_info:
            message = f"{message} ({safety_info})"
        raise _build_empty_response_error(
            response,
            max_output_tokens=max_output_tokens,
            message=message,
        ) from exc
    if isinstance(text, str) and text.strip():
        return text.strip()

    candidates: Sequence[Any] = cast(
        Sequence[Any],
        getattr(response, "candidates", None) or [],
    )
    parts: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                parts.append(part_text.strip())

    if parts:
        return "\n".join(parts).strip()
    safety_info = _format_safety_info(response, max_output_tokens=max_output_tokens)
    message = "Gemini response did not contain text"
    if safety_info:
        message = f"{message} ({safety_info})"
    raise _build_empty_response_error(
        response,
        max_output_tokens=max_output_tokens,
        message=message,
    )


def _format_safety_info(response: Any, *, max_output_tokens: int | None = None) -> str:
    """
    Best-effort summary of safety/finish metadata for error messages.
    """
    pieces: list[str] = []
    candidates: Sequence[Any] = cast(
        Sequence[Any],
        getattr(response, "candidates", None) or [],
    )
    candidate = candidates[0] if candidates else None
    if candidate is not None:
        finish_reason = _format_finish_reason(getattr(candidate, "finish_reason", None))
        if finish_reason:
            pieces.append(f"finish_reason={finish_reason}")
        ratings_summary = _summarize_safety_ratings(
            getattr(candidate, "safety_ratings", None)
        )
        if ratings_summary:
            pieces.append(f"safety_ratings={ratings_summary}")

    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is not None:
        pieces.append(f"usage={_summarize_usage_metadata(usage_metadata)}")
    if max_output_tokens is not None:
        pieces.append(f"max_output_tokens={max_output_tokens}")

    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is None:
        prompt_feedback = getattr(response, "safety_feedback", None)
    if prompt_feedback is not None:
        pieces.append(f"prompt_feedback={prompt_feedback}")

    return "; ".join(pieces)


def _format_finish_reason(value: Any) -> str:
    """Normalize finish_reason values from SDK enums or raw primitives."""
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def _extract_finish_reason(response: Any) -> Optional[str]:
    """
    Extract the finish_reason from the first candidate when available.

    Input: raw SDK response object or dict.
    Output: normalized finish_reason string or None.
    Edge cases: returns None when the response has no candidates or reason.
    """
    candidates: Sequence[Any] = cast(
        Sequence[Any],
        getattr(response, "candidates", None) or [],
    )
    candidate = candidates[0] if candidates else None
    if candidate is None:
        return None
    finish_reason = _format_finish_reason(getattr(candidate, "finish_reason", None))
    return finish_reason or None


def _extract_usage_metadata_counts(
    usage_metadata: Any,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Extract prompt, total, and thoughts token counts from usage metadata.

    Input: usage metadata object or mapping.
    Output: prompt, total, and thoughts token counts, each optional.
    Edge cases: missing or invalid values yield None.
    """
    if usage_metadata is None:
        return None, None, None
    prompt_token_count = _usage_value(usage_metadata, USAGE_PROMPT_TOKEN_KEYS)
    total_token_count = _usage_value(usage_metadata, USAGE_TOTAL_TOKEN_KEYS)
    thoughts_token_count = _usage_value(usage_metadata, USAGE_THOUGHTS_TOKEN_KEYS)
    return prompt_token_count, total_token_count, thoughts_token_count


def _is_token_starvation(
    *,
    finish_reason: str | None,
    thoughts_token_count: int | None,
    max_output_tokens: int | None,
) -> bool:
    """
    Determine whether an empty response was likely caused by token starvation.

    Inputs: finish_reason, thoughts token count, and max output tokens.
    Output: True when the finish reason is MAX_TOKENS and thoughts consumed the budget.
    Edge cases: missing values return False.
    """
    if finish_reason != FINISH_REASON_MAX_TOKENS:
        return False
    if thoughts_token_count is None or max_output_tokens is None:
        return False
    return thoughts_token_count >= max_output_tokens * TOKEN_STARVATION_THRESHOLD_FRACTION


def _build_empty_response_error(
    response: Any,
    *,
    max_output_tokens: int | None,
    message: str,
) -> GeminiEmptyResponseError:
    """
    Build a GeminiEmptyResponseError from a response and derived metadata.

    Inputs: response object, output token cap, and error message.
    Output: GeminiEmptyResponseError populated with finish and usage metadata.
    Edge cases: missing usage or finish metadata yields None values.
    """
    finish_reason = _extract_finish_reason(response)
    usage_metadata = getattr(response, "usage_metadata", None)
    prompt_token_count, total_token_count, thoughts_token_count = (
        _extract_usage_metadata_counts(usage_metadata)
    )
    is_token_starvation = _is_token_starvation(
        finish_reason=finish_reason,
        thoughts_token_count=thoughts_token_count,
        max_output_tokens=max_output_tokens,
    )
    return GeminiEmptyResponseError(
        message,
        finish_reason=finish_reason,
        prompt_token_count=prompt_token_count,
        total_token_count=total_token_count,
        thoughts_token_count=thoughts_token_count,
        is_token_starvation=is_token_starvation,
    )


def _summarize_safety_ratings(ratings: Any) -> str:
    """
    Build a compact, human-readable summary of safety ratings when present.
    """
    if not ratings:
        return ""
    summaries: list[str] = []
    for rating in ratings:
        category = getattr(rating, "category", None)
        probability = getattr(rating, "probability", None)
        blocked = getattr(rating, "blocked", None)
        parts: list[str] = []
        if category is not None:
            parts.append(str(category))
        if probability is not None:
            parts.append(f"prob={probability}")
        if blocked is not None:
            parts.append(f"blocked={blocked}")
        if parts:
            summaries.append("/".join(parts))
    return ", ".join(summaries)


def _summarize_usage_metadata(usage_metadata: Any) -> str:
    """Summarize usage metadata for debugging failures."""
    summary_parts: list[str] = []
    usage_mapping: Optional[Mapping[str, Any]] = None
    if isinstance(usage_metadata, Mapping):
        usage_mapping = cast(Mapping[str, Any], usage_metadata)
    usage_object = cast(object, usage_metadata)

    prompt_tokens = getattr(usage_object, "prompt_token_count", None)
    if prompt_tokens is None and usage_mapping is not None:
        prompt_tokens = usage_mapping.get("prompt_token_count")
    total_tokens = getattr(usage_object, "total_token_count", None)
    if total_tokens is None and usage_mapping is not None:
        total_tokens = usage_mapping.get("total_token_count")
    thoughts_tokens = getattr(usage_object, "thoughts_token_count", None)
    if thoughts_tokens is None and usage_mapping is not None:
        thoughts_tokens = usage_mapping.get("thoughts_token_count")

    if prompt_tokens is not None:
        summary_parts.append(f"prompt={prompt_tokens}")
    if total_tokens is not None:
        summary_parts.append(f"total={total_tokens}")
    if thoughts_tokens is not None:
        summary_parts.append(f"thoughts={thoughts_tokens}")
    return ",".join(summary_parts)


def _extract_usage(response: Any) -> dict[str, int | str]:
    """
    Extract usage metadata from a Gemini API response.

    Input: raw SDK response object or dict.
    Output: token usage dict, empty when
    usage metadata is unavailable or malformed.
    Edge cases: handles dict- and attribute-style responses.
    """
    usage: dict[str, int | str] = {}

    usage_metadata: Any | None = None
    if isinstance(response, Mapping):
        response_mapping = cast(Mapping[str, Any], response)
        usage_metadata = response_mapping.get("usage_metadata")
    else:
        usage_metadata = getattr(cast(object, response), "usage_metadata", None)

    if usage_metadata is not None:
        prompt_tokens = _usage_value(usage_metadata, USAGE_PROMPT_TOKEN_KEYS)
        candidate_tokens = _usage_value(usage_metadata, USAGE_CANDIDATE_TOKEN_KEYS)
        thoughts_tokens = _usage_value(usage_metadata, USAGE_THOUGHTS_TOKEN_KEYS)
        total_tokens = _usage_value(usage_metadata, USAGE_TOTAL_TOKEN_KEYS)
        if prompt_tokens is not None:
            usage["input_tokens"] = prompt_tokens
        if candidate_tokens is not None:
            usage["output_tokens"] = candidate_tokens
        if thoughts_tokens is not None:
            usage["thoughts_tokens"] = thoughts_tokens
        if total_tokens is not None:
            usage["total_tokens"] = total_tokens
    finish_reason = _extract_finish_reason(response)
    if finish_reason:
        usage["finish_reason"] = finish_reason

    return usage


def _usage_value(source: Any, keys: Sequence[str]) -> Optional[int]:
    """
    Return the first valid integer value for a list of possible keys.

    Input: a source object/dict and candidate keys.
    Output: an int or None if no valid value is found or the values are not coercible.
    Edge cases: ignores non-integer values that fail conversion.
    """
    source_mapping: Optional[Mapping[str, Any]] = None
    source_object: Any = source
    if isinstance(source, Mapping):
        source_mapping = cast(Mapping[str, Any], source)
        source_object = None

    for key in keys:
        if source_mapping is not None:
            value = source_mapping.get(key)
        else:
            value = getattr(source_object, key, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None
