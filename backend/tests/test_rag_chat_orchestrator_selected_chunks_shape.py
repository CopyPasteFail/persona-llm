"""Boundary tests for canonical selected-chunk shape in chat orchestration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pytest

from api import llm
from api.prompts import CONTEXT_HEADER, CONTEXT_ONLY_INSTRUCTION
from api import rag_chat_orchestrator
from api import retrieval

PERSONA_NAME = "Test Persona"
MAX_INPUT_TOKENS = 1000
MAX_OUTPUT_TOKENS = 128
WEIGHTED_SCORE_THRESHOLD = 0.62
BM25_SCORE_THRESHOLD = 3.0


class _DeterministicEmbeddingClient:
    """Embedding client test double that returns a stable one-dimensional vector."""

    def embed(self, text: str) -> List[float]:
        del text
        return [1.0]


class _DeterministicVectorClient:
    """Vector client test double that always returns one known chunk id."""

    def query(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        del embedding
        del top_k
        return [{"chunk_id": "chunk-guardrail", "distance": 0.1}]


class _SpyLlmBackend:
    """LLM backend test double that tracks calls and captures final prompt payloads."""

    def __init__(self) -> None:
        self.call_count = 0
        self.prompt_payloads: list[llm.PromptPayload] = []

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        del max_output_tokens
        del thinking_budget_tokens
        self.call_count += 1
        copied_messages = [
            {
                "role": str(message.get("role", "")),
                "content": str(message.get("content", "")),
            }
            for message in prompt_payload.get("messages", [])
        ]
        self.prompt_payloads.append({"messages": copied_messages})
        usage_metadata: llm.UsageMetadata = {
            "input_tokens": 10,
            "output_tokens": 20,
        }
        return "TLDR: stub answer\nWrap: stub wrap", usage_metadata


def _assert_canonical_flat_chunk(selected_chunk: Dict[str, Any]) -> None:
    """Assert selected chunk payload uses canonical flat runtime shape.

    Inputs:
    - selected_chunk: Chunk payload flowing through the orchestrator boundary.

    Outputs:
    - Assertion-only helper; raises when the chunk shape is non-canonical.

    Edge cases:
    - Ensures legacy keys are absent while required flat keys remain available.
    """

    assert "id" not in selected_chunk
    assert "metadata" not in selected_chunk
    assert isinstance(selected_chunk.get("chunk_id"), str)
    assert isinstance(selected_chunk.get("text"), str)


def _assert_final_payload_has_no_legacy_chunk_keys(prompt_payload: llm.PromptPayload) -> None:
    """Assert final LLM payload messages do not serialize legacy chunk keys.

    Inputs:
    - prompt_payload: Payload passed to the final llm backend `generate` call.

    Outputs:
    - Assertion-only helper; raises when serialized message content leaks legacy keys.

    Edge cases:
    - Only context-bearing sections are scanned, identified by prompt markers.
    - Context section extraction uses `Context:` as start marker and trims at
      `Only use facts that appear in Context.` when present.
    """

    context_sections: list[str] = []
    for message in prompt_payload.get("messages", []):
        message_content = str(message.get("content", ""))
        if CONTEXT_HEADER not in message_content:
            continue

        _, _, section_after_header = message_content.partition(CONTEXT_HEADER)
        context_section = section_after_header
        if CONTEXT_ONLY_INSTRUCTION in section_after_header:
            context_section, _, _ = section_after_header.partition(CONTEXT_ONLY_INSTRUCTION)
        context_sections.append(context_section)

    assert context_sections, "Expected at least one context-bearing message in prompt payload."
    for context_section in context_sections:
        assert '"id":' not in context_section
        assert "'id':" not in context_section
        assert '"metadata":' not in context_section
        assert "'metadata':" not in context_section


def test_run_rag_chat_passes_canonical_selected_chunks_at_prompt_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected chunks should stay canonical through prompt and final llm boundaries.

    Inputs:
    - Real retrieval module configured with deterministic embedding/vector clients.
    - Monkeypatched prompt builder that captures selected chunks at the prompt seam.
    - Spy backend that captures final prompt payloads at the llm `generate` seam.

    Outputs:
    - Assertions over chunks passed into llm.build_llm_prompt and ChatResult.
    - Assertions over final serialized payload delivered to llm backend.

    Edge cases:
    - Final llm seam carries only serialized messages, so this test validates both:
      canonical chunk dict shape before serialization and absence of legacy key
      serialization in the final payload.
    """

    captured_prompt_chunks: list[dict[str, Any]] = []
    original_build_llm_prompt = llm.build_llm_prompt

    def _capture_prompt_builder(
        question: str,
        selected_chunks: list[dict[str, Any]],
        *,
        persona_name: str,
        max_input_tokens: Optional[int] = None,
    ) -> llm.PromptPayload:
        captured_prompt_chunks.clear()
        captured_prompt_chunks.extend(dict(chunk) for chunk in selected_chunks)
        return original_build_llm_prompt(
            question,
            selected_chunks,
            persona_name=persona_name,
            max_input_tokens=max_input_tokens,
        )

    retrieval.configure_chunk_store(
        [
            {
                "chunk_id": "chunk-guardrail",
                "text": "I built a Kubernetes platform with Terraform.",
                "profile": "infra",
                "section": "Experience",
            }
        ]
    )
    retrieval.configure_embedding_client(_DeterministicEmbeddingClient())
    retrieval.configure_vector_client(_DeterministicVectorClient())
    monkeypatch.setattr(llm, "build_llm_prompt", _capture_prompt_builder)

    llm_backend = _SpyLlmBackend()
    try:
        chat_result = rag_chat_orchestrator.run_rag_chat(
            "Tell me about your Kubernetes platform work.",
            retrieval=retrieval,
            llm_backend=llm_backend,
            top_k=1,
            persona_name=PERSONA_NAME,
            max_input_tokens=MAX_INPUT_TOKENS,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            enable_thinking_gating=False,
            default_thinking_budget_tokens=None,
            enable_llm_call_gating=False,
            weighted_score_threshold=WEIGHTED_SCORE_THRESHOLD,
            bm25_score_threshold=BM25_SCORE_THRESHOLD,
        )
    finally:
        retrieval.configure_embedding_client(None)
        retrieval.configure_vector_client(None)
        retrieval.configure_chunk_store(None)

    assert llm_backend.call_count == 1
    assert llm_backend.prompt_payloads
    for prompt_payload in llm_backend.prompt_payloads:
        _assert_final_payload_has_no_legacy_chunk_keys(prompt_payload)
    assert captured_prompt_chunks
    for prompt_chunk in captured_prompt_chunks:
        _assert_canonical_flat_chunk(prompt_chunk)
    for response_chunk in chat_result.selected_chunks:
        _assert_canonical_flat_chunk(response_chunk)
