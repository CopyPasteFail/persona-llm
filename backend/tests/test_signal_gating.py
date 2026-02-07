"""Tests for deterministic signal gating in the RAG chat orchestrator."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from api import rag_chat_orchestrator
from api import llm

WEAK_SIGNAL_SCORE = 0.56
WEAK_SIGNAL_BM25 = 1.4
STRONG_SIGNAL_SCORE = 0.66
STRONG_SIGNAL_BM25 = 7.5
TEST_WEIGHTED_SCORE_THRESHOLD = 0.62
TEST_BM25_SCORE_THRESHOLD = 3.0
DEFAULT_TOP_K = 4
MAX_INPUT_TOKENS = 1000
MAX_OUTPUT_TOKENS = 128
PERSONA_NAME = "Test Persona"


class _StaticRetrieval:
    """Retrieval test double that returns static ranked chunks."""

    def __init__(self, selected_chunks: List[Dict[str, Any]]) -> None:
        self._selected_chunks = selected_chunks

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
        return list(self._selected_chunks)

    def has_signal(self, selected: List[Dict[str, Any]]) -> bool:
        return bool(selected)


class _SpyLlmBackend:
    """LLM backend test double that tracks call count."""

    def __init__(self) -> None:
        self.call_count = 0

    def generate(
        self,
        prompt_payload: llm.PromptPayload,
        max_output_tokens: int,
        thinking_budget_tokens: int | None = None,
    ) -> tuple[str, llm.UsageMetadata]:
        self.call_count += 1
        usage_metadata: llm.UsageMetadata = {
            "input_tokens": 10,
            "output_tokens": 20,
        }
        return "TLDR: stub answer\nWrap: stub wrap", usage_metadata


def _build_chunk(
    *, score: float, bm25_score: float, vector_score: float = 0.5
) -> Dict[str, Any]:
    """Build a minimal ranked chunk payload for signal-gating tests."""
    return {
        "id": "chunk-1",
        "text": "sample context",
        "metadata": {},
        "score": score,
        "bm25_score": bm25_score,
        "vector_score": vector_score,
    }


def test_compute_signal_shadow_decision_returns_no_candidates_for_empty_selection() -> None:
    """Shadow decision should skip LLM and report no_candidates when no chunks exist."""
    decision = rag_chat_orchestrator._compute_signal_shadow_decision(  # pyright: ignore[reportPrivateUsage]
        [],
        weighted_score_threshold=TEST_WEIGHTED_SCORE_THRESHOLD,
        bm25_score_threshold=TEST_BM25_SCORE_THRESHOLD,
    )

    assert decision.would_skip_llm is True
    assert decision.reason == rag_chat_orchestrator.SIGNAL_GATE_REASON_NO_CANDIDATES
    assert decision.top1_weighted_score is None
    assert decision.top1_bm25_score is None
    assert decision.top1_vector_score is None


def test_compute_signal_shadow_decision_returns_pass_for_strong_signal() -> None:
    """Shadow decision should pass when score or BM25 crosses configured thresholds."""
    strong_chunk = _build_chunk(score=STRONG_SIGNAL_SCORE, bm25_score=WEAK_SIGNAL_BM25)
    decision = rag_chat_orchestrator._compute_signal_shadow_decision(  # pyright: ignore[reportPrivateUsage]
        [strong_chunk],
        weighted_score_threshold=TEST_WEIGHTED_SCORE_THRESHOLD,
        bm25_score_threshold=TEST_BM25_SCORE_THRESHOLD,
    )

    assert decision.would_skip_llm is False
    assert decision.reason == rag_chat_orchestrator.SIGNAL_GATE_REASON_PASS
    assert decision.top1_weighted_score == STRONG_SIGNAL_SCORE
    assert decision.top1_bm25_score == WEAK_SIGNAL_BM25


def test_compute_signal_shadow_decision_returns_score_below_for_weak_signal() -> None:
    """Shadow decision should skip LLM when both top-1 score and BM25 are weak."""
    weak_chunk = _build_chunk(score=WEAK_SIGNAL_SCORE, bm25_score=WEAK_SIGNAL_BM25)
    decision = rag_chat_orchestrator._compute_signal_shadow_decision(  # pyright: ignore[reportPrivateUsage]
        [weak_chunk],
        weighted_score_threshold=TEST_WEIGHTED_SCORE_THRESHOLD,
        bm25_score_threshold=TEST_BM25_SCORE_THRESHOLD,
    )

    assert decision.would_skip_llm is True
    assert (
        decision.reason == rag_chat_orchestrator.SIGNAL_GATE_REASON_SCORE_BELOW_THRESHOLD
    )


def _run_chat(
    *,
    question: str,
    selected_chunks: List[Dict[str, Any]],
    enable_llm_call_gating: bool,
) -> tuple[rag_chat_orchestrator.ChatResult, _SpyLlmBackend]:
    """Run orchestrator chat with deterministic retrieval and spy LLM backend."""
    retrieval = _StaticRetrieval(selected_chunks=selected_chunks)
    llm_backend = _SpyLlmBackend()
    chat_result = rag_chat_orchestrator.run_rag_chat(
        question,
        retrieval=retrieval,
        llm_backend=llm_backend,
        top_k=DEFAULT_TOP_K,
        persona_name=PERSONA_NAME,
        max_input_tokens=MAX_INPUT_TOKENS,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        enable_thinking_gating=False,
        default_thinking_budget_tokens=None,
        enable_llm_call_gating=enable_llm_call_gating,
        weighted_score_threshold=TEST_WEIGHTED_SCORE_THRESHOLD,
        bm25_score_threshold=TEST_BM25_SCORE_THRESHOLD,
    )
    return chat_result, llm_backend


def test_signal_gating_returns_no_signal_for_weak_top1_weighted_scores() -> None:
    """Weak top-1 scores should skip LLM and return deterministic no-signal fallback."""
    weak_chunk = _build_chunk(score=WEAK_SIGNAL_SCORE, bm25_score=WEAK_SIGNAL_BM25)
    chat_result, llm_backend = _run_chat(
        question="Explain this for a kindergarten class.",
        selected_chunks=[weak_chunk],
        enable_llm_call_gating=True,
    )

    assert llm_backend.call_count == 0
    assert chat_result.response.answer == rag_chat_orchestrator.NO_SIGNAL_ANSWER
    assert chat_result.response.citations == []
    assert (
        chat_result.signal_gate_reason
        == rag_chat_orchestrator.SIGNAL_GATE_REASON_SCORE_BELOW_THRESHOLD
    )
    assert chat_result.top1_weighted_score == WEAK_SIGNAL_SCORE
    assert chat_result.top1_bm25_score == WEAK_SIGNAL_BM25
    assert chat_result.usage_detail == {"total_tokens": None, "finish_reason": None}


def test_signal_gating_allows_signal_when_top1_weighted_score_or_bm25_is_strong() -> None:
    """Signal should pass when either top-1 score or top-1 BM25 crosses threshold."""
    score_strong_chunk = _build_chunk(
        score=STRONG_SIGNAL_SCORE,
        bm25_score=WEAK_SIGNAL_BM25,
    )
    score_result, score_backend = _run_chat(
        question="What did you do in marketing?",
        selected_chunks=[score_strong_chunk],
        enable_llm_call_gating=True,
    )

    bm25_strong_chunk = _build_chunk(
        score=WEAK_SIGNAL_SCORE,
        bm25_score=STRONG_SIGNAL_BM25,
    )
    bm25_result, bm25_backend = _run_chat(
        question="What did you do in marketing?",
        selected_chunks=[bm25_strong_chunk],
        enable_llm_call_gating=True,
    )

    assert score_backend.call_count == 1
    assert bm25_backend.call_count == 1
    assert score_result.response.answer != rag_chat_orchestrator.NO_SIGNAL_ANSWER
    assert bm25_result.response.answer != rag_chat_orchestrator.NO_SIGNAL_ANSWER
    assert score_result.signal_gate_reason == rag_chat_orchestrator.SIGNAL_GATE_REASON_PASS
    assert bm25_result.signal_gate_reason == rag_chat_orchestrator.SIGNAL_GATE_REASON_PASS


def test_signal_gating_flag_controls_whether_weak_signal_skips_llm() -> None:
    """Weak signal should skip LLM only when signal gating is enabled."""
    weak_chunk = _build_chunk(score=WEAK_SIGNAL_SCORE, bm25_score=WEAK_SIGNAL_BM25)
    gated_result, gated_backend = _run_chat(
        question="Tell me about your particle-physics Nobel Prize.",
        selected_chunks=[weak_chunk],
        enable_llm_call_gating=True,
    )
    ungated_result, ungated_backend = _run_chat(
        question="Tell me about your particle-physics Nobel Prize.",
        selected_chunks=[weak_chunk],
        enable_llm_call_gating=False,
    )

    assert gated_backend.call_count == 0
    assert gated_result.response.answer == rag_chat_orchestrator.NO_SIGNAL_ANSWER
    assert ungated_backend.call_count == 1
    assert ungated_result.response.answer != rag_chat_orchestrator.NO_SIGNAL_ANSWER
    assert gated_result.signal_would_skip_llm is True
    assert ungated_result.signal_would_skip_llm is True
