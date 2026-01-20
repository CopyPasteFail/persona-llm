from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

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

_SNIPPET_CHAR_LIMIT = 320


class RetrievalPipeline(Protocol):
    """Protocol for retrieval helpers used by the orchestrator."""

    def normalize_question_for_first_person(self, q: str) -> str: ...

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


def run_rag_chat(
    question: str,
    *,
    retrieval: RetrievalPipeline,
    llm_backend: LlmBackend,
    top_k: int,
    persona_name: str,
    max_input_tokens: Optional[int],
    max_output_tokens: int,
) -> ChatResult:
    """Run a RAG chat flow with injected retrieval + LLM backends."""
    normalized_question = retrieval.normalize_question_for_first_person(
        (question or "").strip()
    )

    # Temporary debug override: uncomment to send a raw hello-world prompt.
    # prompt_payload = {"messages": [{"role": "user", "content": "hello world"}]}
    # answer_text, usage_meta = llm_backend.generate(
    #     prompt_payload,
    #     max_output_tokens=max_output_tokens,
    # )
    # answer_final = answer_text.strip() or UNABLE_TO_GENERATE_ANSWER
    # usage = _usage_from_llm_meta(
    #     usage_meta,
    #     question="hello world",
    #     answer=answer_final,
    # )
    # return ChatResult(
    #     response=ChatResponse(
    #         answer=answer_final,
    #         citations=[],
    #         usage=usage,
    #         input_token_limit=max_input_tokens,
    #     ),
    #     selected_chunks=[],
    #     normalized_question=normalized_question,
    # )

    query_embedding = retrieval.embed_query(normalized_question)
    candidate_chunks = retrieval.search_vector_store(query_embedding, top_k=top_k)
    selected_chunks = retrieval.apply_filters_and_boosting(candidate_chunks)

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
    )
    answer_final = answer_text.strip() or UNABLE_TO_GENERATE_ANSWER

    citations = [_chunk_to_citation(chunk) for chunk in selected_chunks]
    usage = _usage_from_llm_meta(
        usage_meta,
        question=normalized_question,
        answer=answer_final,
    )

    return ChatResult(
        response=ChatResponse(
            answer=answer_final,
            citations=citations,
            usage=usage,
            input_token_limit=max_input_tokens,
        ),
        selected_chunks=selected_chunks,
        normalized_question=normalized_question,
    )


def _usage_from_llm_meta(meta: Dict[str, int], *, question: str, answer: str) -> Usage:
    """Build usage metrics from LLM metadata or fallback estimation."""
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
    """Convert a retrieval chunk into a response citation."""
    chunk_id = str(chunk.get("id") or "")
    text = str(chunk.get("text") or "").strip()
    snippet = " ".join(text.split())
    if snippet and len(snippet) > _SNIPPET_CHAR_LIMIT:
        snippet = snippet[: _SNIPPET_CHAR_LIMIT - 3].rstrip() + "..."
    return Citation(id=chunk_id, text=snippet or None)
