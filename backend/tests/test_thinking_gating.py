"""Tests for deterministic thinking-budget gating."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from api import llm
from api import rag_chat_orchestrator
from api.llm import THINKING_BUDGET_DISABLED, _GeminiGenaiClient  # pyright: ignore[reportPrivateUsage]
from api.settings import settings


class _StubRetrieval:
    """Minimal retrieval stub that always returns a single chunk."""

    def normalize_question_for_first_person(self, question: str) -> str:
        return question

    def embed_query(self, question: str) -> Optional[List[float]]:
        return [1.0]

    def search_vector_store(
        self, embedding: Optional[Sequence[float]], top_k: int
    ) -> List[Dict[str, Any]]:
        return [{"id": "chunk-1", "distance": 0.1}]

    def apply_filters_and_boosting(
        self, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        return [{"id": "chunk-1", "text": "stub chunk", "metadata": {}}]

    def has_signal(self, selected: List[Dict[str, Any]]) -> bool:
        return bool(selected)


class _CapturingLlmBackend:
    """LLM backend stub that captures the thinking-budget override."""

    def __init__(self) -> None:
        self.thinking_budget_tokens: int | None = None

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        del prompt_payload
        del max_output_tokens
        self.thinking_budget_tokens = thinking_budget_tokens
        usage_metadata: llm.UsageMetadata = {"input_tokens": 1, "output_tokens": 1}
        return "stub answer", usage_metadata


def test_choose_thinking_budget_tokens_returns_zero_for_simple_question() -> None:
    """Simple short question should disable thinking."""
    default_budget = 300
    result = rag_chat_orchestrator.choose_thinking_budget_tokens(
        "What is caching?",
        default_budget=default_budget,
        selected_chunks_count=1,
    )
    assert result == 0


def test_choose_thinking_budget_tokens_returns_default_for_reasoning_keyword() -> None:
    """Reasoning keywords should keep the default budget."""
    default_budget = 300
    result = rag_chat_orchestrator.choose_thinking_budget_tokens(
        "How would you design this system?",
        default_budget=default_budget,
        selected_chunks_count=1,
    )
    assert result == default_budget


def test_choose_thinking_budget_tokens_returns_default_for_long_question() -> None:
    """Long questions should keep the default budget."""
    default_budget = 300
    question = "a" * rag_chat_orchestrator.SIMPLE_QUESTION_CHAR_LIMIT
    result = rag_chat_orchestrator.choose_thinking_budget_tokens(
        question,
        default_budget=default_budget,
        selected_chunks_count=1,
    )
    assert result == default_budget


def test_choose_thinking_budget_tokens_returns_default_for_multiple_question_marks() -> None:
    """Multiple question marks should keep the default budget."""
    default_budget = 300
    result = rag_chat_orchestrator.choose_thinking_budget_tokens(
        "What is caching??",
        default_budget=default_budget,
        selected_chunks_count=1,
    )
    assert result == default_budget


def test_gating_passes_zero_budget_for_simple_question() -> None:
    """Gating should pass a zero thinking budget for simple questions."""
    retrieval = _StubRetrieval()
    backend = _CapturingLlmBackend()

    result = rag_chat_orchestrator.run_rag_chat(
        "What is caching?",
        retrieval=retrieval,
        llm_backend=backend,
        top_k=1,
        persona_name="Test Persona",
        max_input_tokens=1000,
        max_output_tokens=128,
        enable_thinking_gating=True,
        default_thinking_budget_tokens=300,
    )

    assert backend.thinking_budget_tokens == 0
    assert result.thinking_budget_tokens_effective == 0


class _FakeThinkingConfig:
    def __init__(self, *, include_thoughts: bool, thinking_budget: int) -> None:
        self.include_thoughts = include_thoughts
        self.thinking_budget = thinking_budget


class _FakeGenerateContentConfig:
    def __init__(
        self,
        *,
        system_instruction: str | None,
        max_output_tokens: int,
        temperature: float,
        top_p: float,
    ) -> None:
        self.system_instruction = system_instruction
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.thinking_config: _FakeThinkingConfig | None = None


class _FakeTypes:
    GenerateContentConfig = _FakeGenerateContentConfig
    ThinkingConfig = _FakeThinkingConfig


def test_generate_config_uses_zero_budget_and_disables_thoughts_on_override() -> None:
    """Override=0 should force thinking_budget=0 and include_thoughts=False."""
    client = _GeminiGenaiClient(
        project="test",
        region="test",
        model_name="test",
        thinking_budget_tokens=300,
    )
    config, effective_budget, include_thoughts = client._build_generate_config(  # pyright: ignore[reportPrivateUsage]
        system_prompt="S",
        max_output_tokens=64,
        thinking_budget_tokens=0,
        types_module=_FakeTypes,
    )
    assert config.thinking_config is not None
    assert effective_budget == THINKING_BUDGET_DISABLED
    assert include_thoughts is False
    assert config.thinking_config.thinking_budget == 0
    assert config.thinking_config.include_thoughts is False


def test_generate_config_uses_override_budget_and_include_thoughts() -> None:
    """Override>0 should keep include_thoughts from settings."""
    original_include_thoughts = settings.INCLUDE_THOUGHTS
    settings.INCLUDE_THOUGHTS = True
    try:
        client = _GeminiGenaiClient(
            project="test",
            region="test",
            model_name="test",
            thinking_budget_tokens=300,
        )
        config, effective_budget, include_thoughts = client._build_generate_config(  # pyright: ignore[reportPrivateUsage]
            system_prompt="S",
            max_output_tokens=64,
            thinking_budget_tokens=300,
            types_module=_FakeTypes,
        )
        assert config.thinking_config is not None
        assert effective_budget == 300
        assert include_thoughts is True
        assert config.thinking_config.thinking_budget == 300
        assert config.thinking_config.include_thoughts is True
    finally:
        settings.INCLUDE_THOUGHTS = original_include_thoughts


def test_generate_config_uses_client_default_when_override_none() -> None:
    """Override=None should use the client default and attach thinking config."""
    client = _GeminiGenaiClient(
        project="test",
        region="test",
        model_name="test",
        thinking_budget_tokens=120,
    )
    config, effective_budget, include_thoughts = client._build_generate_config(  # pyright: ignore[reportPrivateUsage]
        system_prompt="S",
        max_output_tokens=64,
        thinking_budget_tokens=None,
        types_module=_FakeTypes,
    )
    assert config.thinking_config is not None
    assert effective_budget == 120
    assert include_thoughts in {True, False}
    assert config.thinking_config.thinking_budget == 120
