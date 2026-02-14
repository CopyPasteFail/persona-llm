"""Tests for deterministic duration routing in chat orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from api import llm
from api import rag_chat_orchestrator


class _DurationRoutingRetrievalSpy:
    """Retrieval spy for verifying duration routing short-circuit behavior."""

    def __init__(
        self, snapshot: Mapping[str, Mapping[str, Any]] | None = None
    ) -> None:
        self.embed_called = False
        self.search_called = False
        self.apply_called = False
        self.snapshot_called = False
        self._snapshot: Mapping[str, Mapping[str, Any]] = snapshot or {
            "chunk-1": {
                "id": "chunk-1",
                "text": "stub",
                "metadata": {
                    "profile": "infra",
                    "section": "Experience",
                    "start_year": 2023,
                    "end_year": None,
                    "extras": {
                        "stint_domains": ["devops"],
                        "employer": "Acme",
                        "title": "SRE",
                    },
                },
            }
        }

    def normalize_question_for_first_person(self, question: str) -> str:
        return question

    def embed_query(self, question: str) -> Optional[List[float]]:
        self.embed_called = True
        return [1.0]

    def search_vector_store(
        self, embedding: Optional[Sequence[float]], top_k: int
    ) -> List[Dict[str, Any]]:
        self.search_called = True
        return [{"id": "chunk-1", "distance": 0.2}]

    def apply_filters_and_boosting(
        self, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        self.apply_called = True
        return [{"id": "chunk-1", "text": "stub", "metadata": {}}]

    def has_selected_chunks(self, selected: List[Dict[str, Any]]) -> bool:
        return bool(selected)

    def get_chunk_store_snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        self.snapshot_called = True
        return self._snapshot


class _LlmSpy:
    """LLM spy used to verify deterministic duration bypass behavior."""

    def __init__(self) -> None:
        self.generate_called = False

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        self.generate_called = True
        usage_metadata: llm.UsageMetadata = {"input_tokens": 5, "output_tokens": 8}
        return "stub answer", usage_metadata


def test_run_rag_chat_duration_query_bypasses_retrieval_and_llm() -> None:
    """Duration query with a mapped domain should bypass retrieval and LLM."""
    retrieval_spy = _DurationRoutingRetrievalSpy()
    llm_spy = _LlmSpy()
    current_year = datetime.now(timezone.utc).year

    result = rag_chat_orchestrator.run_rag_chat(
        "How many years of DevOps experience do you have?",
        retrieval=retrieval_spy,
        llm_backend=llm_spy,
        top_k=4,
        persona_name="Test Persona",
        max_input_tokens=1000,
        max_output_tokens=128,
        enable_thinking_gating=False,
        default_thinking_budget_tokens=None,
        enable_llm_call_gating=True,
    )

    assert result.llm_gate_reason == rag_chat_orchestrator.llm_gate_reason_DURATION_BYPASS
    assert result.response.citations == []
    assert f"{current_year - 2023 + 1}" in result.response.answer
    assert "Union total" in result.response.answer
    assert "Based on:" in result.response.answer
    assert "Acme, SRE, 2023-present" in result.response.answer
    assert retrieval_spy.snapshot_called
    assert not retrieval_spy.embed_called
    assert not retrieval_spy.search_called
    assert not retrieval_spy.apply_called
    assert not llm_spy.generate_called


def test_run_rag_chat_non_duration_query_keeps_existing_path() -> None:
    """Non-duration query should keep regular retrieval + LLM execution path."""
    retrieval_spy = _DurationRoutingRetrievalSpy()
    llm_spy = _LlmSpy()

    result = rag_chat_orchestrator.run_rag_chat(
        "What did you do with Kubernetes?",
        retrieval=retrieval_spy,
        llm_backend=llm_spy,
        top_k=4,
        persona_name="Test Persona",
        max_input_tokens=1000,
        max_output_tokens=128,
        enable_thinking_gating=False,
        default_thinking_budget_tokens=None,
        enable_llm_call_gating=False,
    )

    assert result.llm_gate_reason != rag_chat_orchestrator.llm_gate_reason_DURATION_BYPASS
    assert retrieval_spy.embed_called
    assert retrieval_spy.search_called
    assert retrieval_spy.apply_called
    assert not retrieval_spy.snapshot_called
    assert llm_spy.generate_called


def test_run_rag_chat_generic_duration_query_bypasses_rag_and_llm() -> None:
    """Generic years-of-experience prompts should route through deterministic duration logic."""
    retrieval_spy = _DurationRoutingRetrievalSpy()
    llm_spy = _LlmSpy()

    result = rag_chat_orchestrator.run_rag_chat(
        "How many years of experience?",
        retrieval=retrieval_spy,
        llm_backend=llm_spy,
        top_k=4,
        persona_name="Test Persona",
        max_input_tokens=1000,
        max_output_tokens=128,
        enable_thinking_gating=False,
        default_thinking_budget_tokens=None,
        enable_llm_call_gating=True,
    )

    assert result.llm_gate_reason == rag_chat_orchestrator.llm_gate_reason_DURATION_BYPASS
    assert "TLDR: I have about" in result.response.answer
    assert "Based on: Acme, SRE, 2023-present." in result.response.answer
    assert retrieval_spy.snapshot_called
    assert not retrieval_spy.embed_called
    assert not retrieval_spy.search_called
    assert not retrieval_spy.apply_called
    assert not llm_spy.generate_called


def test_run_rag_chat_duration_query_with_no_matched_stints_returns_guard_answer() -> None:
    """Mapped duration prompts with no matching Experience domains should return a guard answer."""
    retrieval_spy = _DurationRoutingRetrievalSpy(
        snapshot={
            "chunk-1": {
                "id": "chunk-1",
                "text": "stub",
                "metadata": {
                    "profile": "marketing",
                    "section": "Experience",
                    "start_year": 2021,
                    "end_year": 2022,
                    "extras": {
                        "stint_domains": ["marketing"],
                        "employer": "Beta",
                        "title": "Marketing Manager",
                    },
                },
            }
        }
    )
    llm_spy = _LlmSpy()

    result = rag_chat_orchestrator.run_rag_chat(
        "How many years of DevOps experience do you have?",
        retrieval=retrieval_spy,
        llm_backend=llm_spy,
        top_k=4,
        persona_name="Test Persona",
        max_input_tokens=1000,
        max_output_tokens=128,
        enable_thinking_gating=False,
        default_thinking_budget_tokens=None,
        enable_llm_call_gating=True,
    )

    assert result.llm_gate_reason == rag_chat_orchestrator.llm_gate_reason_DURATION_BYPASS
    assert "I can't compute this from the Experience stints in the current dataset." in result.response.answer
    assert "Based on:" not in result.response.answer
    assert result.response.citations == []
    assert retrieval_spy.snapshot_called
    assert not retrieval_spy.embed_called
    assert not retrieval_spy.search_called
    assert not retrieval_spy.apply_called
    assert not llm_spy.generate_called
