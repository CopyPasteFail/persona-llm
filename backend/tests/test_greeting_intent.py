"""Tests for deterministic greeting-only question detection."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from api import llm
from api import rag_chat_orchestrator


def test_is_greeting_only_question_returns_true_for_simple_greeting() -> None:
    """Simple greeting text should be classified as greeting-only.

    What is tested:
        Detection of a standalone greeting token.
    How it's tested:
        Pass a single greeting word.
    Expected result format:
        Helper returns True.
    """
    assert rag_chat_orchestrator._is_greeting_only_question("hello")  # pyright: ignore[reportPrivateUsage]


def test_is_greeting_only_question_returns_true_for_multiword_greeting() -> None:
    """Multiword greetings should be classified as greeting-only.

    What is tested:
        Detection of a recognized two-word greeting phrase.
    How it's tested:
        Pass a greeting phrase with one filler token.
    Expected result format:
        Helper returns True.
    """
    assert rag_chat_orchestrator._is_greeting_only_question("Good morning team")  # pyright: ignore[reportPrivateUsage]


def test_is_greeting_only_question_returns_false_when_request_follows_greeting() -> None:
    """Greeting followed by a real request should not be greeting-only.

    What is tested:
        Distinction between social opener and substantive query.
    How it's tested:
        Pass a greeting plus technical question.
    Expected result format:
        Helper returns False.
    """
    assert not rag_chat_orchestrator._is_greeting_only_question(  # pyright: ignore[reportPrivateUsage]
        "hi, do you know kubernetes?"
    )


def test_is_greeting_only_question_returns_false_for_non_greeting_text() -> None:
    """Non-greeting text should not be classified as greeting-only.

    What is tested:
        Rejection of non-greeting inputs.
    How it's tested:
        Pass a direct question without a greeting prefix.
    Expected result format:
        Helper returns False.
    """
    assert not rag_chat_orchestrator._is_greeting_only_question("what is kubernetes?")  # pyright: ignore[reportPrivateUsage]


def test_is_greeting_only_question_returns_true_for_small_talk_variants() -> None:
    """Small-talk greeting variants should be greeting-only.

    What is tested:
        Detection of colloquial greeting and social check-in phrasing.
    How it's tested:
        Evaluate a list of phrase variants observed in live prompts.
    Expected result format:
        Helper returns True for each variant.
    """
    variants = [
        "wassap?",
        "what's up?",
        "how you doing?",
        "how you doin'",
        "how do you feel?",
    ]
    for variant in variants:
        assert rag_chat_orchestrator._is_greeting_only_question(variant)  # pyright: ignore[reportPrivateUsage]


def test_is_greeting_only_question_returns_false_for_small_talk_plus_topic() -> None:
    """Small-talk opener followed by a topic should not be greeting-only.

    What is tested:
        Rejection when a substantive topic follows a social opener.
    How it's tested:
        Pass a small-talk phrase with a technical follow-up.
    Expected result format:
        Helper returns False.
    """
    assert not rag_chat_orchestrator._is_greeting_only_question(  # pyright: ignore[reportPrivateUsage]
        "what's up with kubernetes?"
    )


class _GreetingAwareRetrievalSpy:
    """Retrieval spy used to verify greeting bypass control flow.

    What this class does:
    - Captures whether retrieval stages are invoked.
    - Returns static chunk data when retrieval is exercised.
    """

    def __init__(self) -> None:
        self.embed_called = False
        self.search_called = False
        self.apply_called = False

    def normalize_question_for_first_person(self, question: str) -> str:
        return question

    def embed_query(self, question: str) -> Optional[List[float]]:
        self.embed_called = True
        return [1.0]

    def search_vector_store(
        self, embedding: Optional[Sequence[float]], top_k: int
    ) -> List[Dict[str, Any]]:
        self.search_called = True
        return [{"chunk_id": "chunk-1", "distance": 0.1}]

    def apply_filters_and_boosting(
        self, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        self.apply_called = True
        return [{"chunk_id": "chunk-1", "text": "stub chunk"}]

    def has_selected_chunks(self, selected: List[Dict[str, Any]]) -> bool:
        return bool(selected)


class _GreetingAwareLlmSpy:
    """LLM spy used to verify greeting bypass control flow."""

    def __init__(self) -> None:
        self.generate_called = False

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        self.generate_called = True
        usage_metadata: llm.UsageMetadata = {"input_tokens": 1, "output_tokens": 1}
        return "stub answer", usage_metadata


def test_run_rag_chat_greeting_only_bypasses_retrieval_and_llm() -> None:
    """Greeting-only input should short-circuit before retrieval and LLM calls.

    What is tested:
        End-to-end greeting-only bypass behavior in run_rag_chat.
    How it's tested:
        Execute run_rag_chat with greeting-only input using retrieval/LLM spies.
    Expected result format:
        Greeting answer returned, retrieval and LLM are not called, and reason is
        greeting_bypass.
    """
    retrieval_spy = _GreetingAwareRetrievalSpy()
    llm_spy = _GreetingAwareLlmSpy()

    result = rag_chat_orchestrator.run_rag_chat(
        "hi",
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

    assert result.response.answer == rag_chat_orchestrator.GREETING_ONLY_ANSWER
    assert result.llm_gate_reason == rag_chat_orchestrator.llm_gate_reason_GREETING_BYPASS
    assert not retrieval_spy.embed_called
    assert not retrieval_spy.search_called
    assert not retrieval_spy.apply_called
    assert not llm_spy.generate_called


def test_run_rag_chat_greeting_plus_question_uses_regular_flow() -> None:
    """Greeting prefix plus question should not trigger greeting-only bypass.

    What is tested:
        Distinction between social-only greeting and substantive request.
    How it's tested:
        Execute run_rag_chat with greeting plus technical query.
    Expected result format:
        Retrieval and LLM are invoked, and response is not greeting-only answer.
    """
    retrieval_spy = _GreetingAwareRetrievalSpy()
    llm_spy = _GreetingAwareLlmSpy()

    result = rag_chat_orchestrator.run_rag_chat(
        "hi, do you know kubernetes?",
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

    assert result.response.answer != rag_chat_orchestrator.GREETING_ONLY_ANSWER
    assert retrieval_spy.embed_called
    assert retrieval_spy.search_called
    assert retrieval_spy.apply_called
    assert llm_spy.generate_called


def test_run_rag_chat_non_latin_input_bypasses_retrieval_and_llm() -> None:
    """Non-Latin input should short-circuit before retrieval and LLM calls.

    What is tested:
        Input guard for unsupported non-Latin scripts in run_rag_chat.
    How it's tested:
        Execute run_rag_chat with Hebrew text using retrieval/LLM spies.
    Expected result format:
        English-only input answer returned, retrieval and LLM are not called,
        and reason is non_english_input.
    """
    retrieval_spy = _GreetingAwareRetrievalSpy()
    llm_spy = _GreetingAwareLlmSpy()

    result = rag_chat_orchestrator.run_rag_chat(
        "\u05e9\u05dc\u05d5\u05dd",
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

    assert result.response.answer == rag_chat_orchestrator.ENGLISH_INPUT_ONLY_ANSWER
    assert (
        result.llm_gate_reason == rag_chat_orchestrator.llm_gate_reason_NON_ENGLISH_INPUT
    )
    assert not retrieval_spy.embed_called
    assert not retrieval_spy.search_called
    assert not retrieval_spy.apply_called
    assert not llm_spy.generate_called
