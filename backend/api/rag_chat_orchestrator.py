from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, TypedDict

from . import llm
from .llm_backends import LlmBackend
from .types import ChatResponse, Citation, Usage

APPROX_CHARS_PER_TOKEN = 4
NO_SIGNAL_ANSWER = (
    "TLDR: I do not have that in my indexed experience right now.\n"
    "- I only summarize what is in my available context.\n"
    "- Ask again with a more specific query.\n"
    "- Or tell me to expand the data source.\n"
    "Wrap: Ask me something that appears in my experience or projects."
)
UNABLE_TO_GENERATE_ANSWER = "TLDR: Unable to generate an answer.\nWrap: Try again shortly."

ELLIPSIS_SUFFIX = "..."
SNIPPET_CHAR_LIMIT = 320
SIMPLE_QUESTION_CHAR_LIMIT = 120
SIMPLE_QUESTION_MAX_QUESTION_MARKS = 1
SIMPLE_QUESTION_KEYWORDS = (
    "compare",
    "tradeoff",
    "design",
    "debug",
    "why",
    "how",
    "step",
    "recommend",
    "pros",
    "cons",
    "architecture",
    "root cause",
)
SIMPLE_QUESTION_PREFIXES = (
    "do you have experience",
    "what is",
    "define",
    "list",
    "summarize",
)


class RetrievalPipeline(Protocol):
    """Protocol for retrieval helpers used by the orchestrator."""

    def normalize_question_for_first_person(self, question_text: str) -> str: ...

    def embed_query(self, question: str) -> Optional[List[float]]: ...

    def search_vector_store(
        self, embedding: Optional[Sequence[float]], top_k: int
    ) -> List[Dict[str, Any]]: ...

    def apply_filters_and_boosting(
        self, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]: ...

    def has_signal(self, selected: List[Dict[str, Any]]) -> bool: ...


@dataclass(frozen=True)
class ChatResult:
    """Orchestrated chat response with selected retrieval chunks."""

    response: ChatResponse
    selected_chunks: List[Dict[str, Any]]
    normalized_question: str
    usage_detail: "UsageDetail"
    thinking_budget_tokens_effective: int | None


class UsageDetail(TypedDict):
    """Backend-only usage detail from provider metadata.

    Fields are optional in provider responses, so values may be None.
    """

    total_tokens: int | None
    finish_reason: str | None


def run_rag_chat(
    question: str,
    *,
    retrieval: RetrievalPipeline,
    llm_backend: LlmBackend,
    top_k: int,
    persona_name: str,
    max_input_tokens: Optional[int],
    max_output_tokens: int,
    enable_thinking_gating: bool,
    default_thinking_budget_tokens: int | None,
) -> ChatResult:
    """Run a RAG chat flow and return the selected context plus response.

    Inputs:
    - question: Raw user prompt (may be empty or whitespace).
    - retrieval: Retrieval implementation providing embedding/search/filtering.
    - llm_backend: LLM backend used to generate a response.
    - top_k: Max number of retrieved chunks to consider.
    - persona_name: Persona name used for prompt generation.
    - max_input_tokens: Optional token budget for the prompt.
    - max_output_tokens: Max tokens to request for the response.
    - enable_thinking_gating: Whether per-request thinking gating is enabled.
    - default_thinking_budget_tokens: Default thinking budget from settings.

    Output:
    - ChatResult containing the response, selected chunks, usage detail, and normalized question.

    Edge cases:
    - Empty/whitespace question is normalized before retrieval.
    - If retrieval returns no signal, a predefined answer is returned with
      estimated usage and no citations.

    Concurrency/atomicity:
    - No shared state is mutated; the flow is safe to call concurrently.
    """
    normalized_question = retrieval.normalize_question_for_first_person(
        (question or "").strip()
    )

    query_embedding = retrieval.embed_query(normalized_question)
    candidate_chunks = retrieval.search_vector_store(query_embedding, top_k=top_k)
    selected_chunks = retrieval.apply_filters_and_boosting(candidate_chunks)
    thinking_budget_tokens_effective = _resolve_thinking_budget_tokens(
        normalized_question,
        selected_chunks_count=len(selected_chunks),
        enable_thinking_gating=enable_thinking_gating,
        default_thinking_budget_tokens=default_thinking_budget_tokens,
    )

    if not retrieval.has_signal(selected_chunks):
        answer = NO_SIGNAL_ANSWER
        usage = Usage(
            input_tokens=max(1, len(normalized_question) // APPROX_CHARS_PER_TOKEN),
            output_tokens=max(1, len(answer) // APPROX_CHARS_PER_TOKEN),
        )
        return ChatResult(
            response=ChatResponse(
                answer=answer,
                citations=[],
                usage=usage,
                input_token_limit=max_input_tokens,
            ),
            selected_chunks=[],
            normalized_question=normalized_question,
            usage_detail=_empty_usage_detail(),
            thinking_budget_tokens_effective=thinking_budget_tokens_effective,
        )

    prompt_payload = llm.build_llm_prompt(
        normalized_question,
        selected_chunks,
        persona_name=persona_name,
        max_input_tokens=max_input_tokens,
    )
    answer_text, usage_meta = llm_backend.generate(
        prompt_payload,
        max_output_tokens=max_output_tokens,
        thinking_budget_tokens=thinking_budget_tokens_effective,
    )
    answer_final = answer_text.strip() or UNABLE_TO_GENERATE_ANSWER

    citations = [_chunk_to_citation(chunk) for chunk in selected_chunks]
    usage = _usage_from_llm_meta(
        usage_meta,
        question=normalized_question,
        answer=answer_final,
    )
    usage_detail = _usage_detail_from_llm_meta(usage_meta)

    return ChatResult(
        response=ChatResponse(
            answer=answer_final,
            citations=citations,
            usage=usage,
            input_token_limit=max_input_tokens,
        ),
        selected_chunks=selected_chunks,
        normalized_question=normalized_question,
        usage_detail=usage_detail,
        thinking_budget_tokens_effective=thinking_budget_tokens_effective,
    )


def choose_thinking_budget_tokens(
    question: str,
    *,
    default_budget: int,
    selected_chunks_count: int,
) -> int:
    """
    Decide the thinking budget for a request using a deterministic heuristic.

    Inputs:
    - question: Normalized question string.
    - default_budget: Thinking budget to use for non-simple questions.
    - selected_chunks_count: Count of selected retrieval chunks (reserved for future use).

    Output:
    - Thinking budget tokens; zero means "disable thinking."

    Edge cases:
    - Simple questions return 0.
    - Non-simple questions return the default budget.
    """
    if _is_simple_question(question):
        return 0
    return int(default_budget)


def _is_simple_question(question: str) -> bool:
    """
    Determine whether a question should skip model thinking.

    Inputs:
    - question: Normalized question string.

    Output:
    - True when the question is short and does not contain reasoning keywords,
      or when it uses a known simple prefix.

    Edge cases:
    - Empty questions are treated as simple.
    - Prefix matching is case-insensitive and ignores surrounding whitespace.
    """
    normalized_question = (question or "").strip()
    if not normalized_question:
        return True

    if len(normalized_question) >= SIMPLE_QUESTION_CHAR_LIMIT:
        return False

    question_marks = normalized_question.count("?")
    if question_marks > SIMPLE_QUESTION_MAX_QUESTION_MARKS:
        return False

    lowered = normalized_question.lower()
    if any(lowered.startswith(prefix) for prefix in SIMPLE_QUESTION_PREFIXES):
        return True

    return not any(keyword in lowered for keyword in SIMPLE_QUESTION_KEYWORDS)


def _resolve_thinking_budget_tokens(
    question: str,
    *,
    selected_chunks_count: int,
    enable_thinking_gating: bool,
    default_thinking_budget_tokens: int | None,
) -> int | None:
    """
    Resolve the effective thinking budget for the request.

    Inputs:
    - question: Normalized question string.
    - selected_chunks_count: Count of selected retrieval chunks.
    - enable_thinking_gating: Feature flag for per-request gating.
    - default_thinking_budget_tokens: Default budget from settings.

    Output:
    - Effective thinking budget or None to use the client default.

    Edge cases:
    - Returns None when no default budget is configured.
    - When gating is disabled, returns the default budget unchanged.
    """
    if default_thinking_budget_tokens is None:
        return None
    if not enable_thinking_gating:
        return int(default_thinking_budget_tokens)
    return choose_thinking_budget_tokens(
        question,
        default_budget=int(default_thinking_budget_tokens),
        selected_chunks_count=selected_chunks_count,
    )


def _usage_from_llm_meta(meta: Dict[str, Any], *, question: str, answer: str) -> Usage:
    """Build usage metrics from LLM metadata with deterministic fallbacks.

    Inputs:
    - meta: Dictionary containing token counts from the LLM backend.
    - question: Normalized question text.
    - answer: Final answer text.

    Output:
    - Usage with non-zero input and output token counts.

    Edge cases:
    - Missing or non-positive token counts fall back to approximate estimates.

    Concurrency/atomicity:
    - Pure computation with no side effects.
    """
    fallback_input = max(1, len(question) // APPROX_CHARS_PER_TOKEN)
    fallback_output = max(1, len(answer) // APPROX_CHARS_PER_TOKEN)
    input_tokens = int(meta.get("input_tokens", fallback_input))
    output_tokens = int(meta.get("output_tokens", fallback_output))
    if input_tokens <= 0:
        input_tokens = fallback_input
    if output_tokens <= 0:
        output_tokens = fallback_output
    thoughts_tokens = meta.get("thoughts_tokens")
    thoughts_tokens_value = int(thoughts_tokens) if thoughts_tokens is not None else None
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thoughts_tokens=thoughts_tokens_value,
    )


def _usage_detail_from_llm_meta(meta: Dict[str, Any]) -> UsageDetail:
    """Build backend-only usage detail from provider metadata.

    Inputs:
    - meta: Dictionary containing token counts and finish reason from the LLM backend.

    Output:
    - UsageDetail with best-effort values for total tokens and finish reason.

    Edge cases:
    - Missing values return None.
    """
    total_tokens_value = meta.get("total_tokens")
    total_tokens = int(total_tokens_value) if total_tokens_value is not None else None
    finish_reason_value = meta.get("finish_reason")
    finish_reason = str(finish_reason_value) if finish_reason_value is not None else None
    return UsageDetail(
        total_tokens=total_tokens,
        finish_reason=finish_reason,
    )


def _empty_usage_detail() -> UsageDetail:
    """Return an empty usage detail payload for non-LLM responses."""
    return UsageDetail(
        total_tokens=None,
        finish_reason=None,
    )


def _chunk_to_citation(chunk: Dict[str, Any]) -> Citation:
    """Convert a retrieval chunk into a response citation.

    Inputs:
    - chunk: Retrieval result containing text and an optional identifier.

    Output:
    - Citation with a compact snippet and id when available.

    Edge cases:
    - Missing or empty text yields a citation with `text=None`.
    - Long text is truncated with an ellipsis to a fixed limit.

    Concurrency/atomicity:
    - Pure computation with no side effects.
    """
    chunk_id = str(chunk.get("id") or "")
    text = str(chunk.get("text") or "").strip()
    snippet = " ".join(text.split())
    if snippet and len(snippet) > SNIPPET_CHAR_LIMIT:
        max_snippet_length = SNIPPET_CHAR_LIMIT - len(ELLIPSIS_SUFFIX)
        snippet = snippet[:max_snippet_length].rstrip() + ELLIPSIS_SUFFIX
    return Citation(id=chunk_id, text=snippet or None)
