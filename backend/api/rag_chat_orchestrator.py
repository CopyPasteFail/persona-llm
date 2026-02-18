from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, TypedDict

from . import deterministic_duration
from . import llm
from .llm_backends import LlmBackend
from .settings import (
    DEFAULT_BM25_SCORE_THRESHOLD,
    DEFAULT_WEIGHTED_CONSENSUS_COUNT,
    DEFAULT_WEIGHTED_SCORE_THRESHOLD,
)
from .types import ChatResponse, Citation, Usage

APPROX_CHARS_PER_TOKEN = 4
NO_SIGNAL_ANSWER = (
    "TLDR: I could not find a clear match for that in my current indexed experience.\n"
    "- I can only answer from the context that is currently available to me.\n"
    "- If you ask with a bit more detail, I can try again.\n"
)
GREETING_ONLY_ANSWER = (
    "Hi, happy to chat.\n"
    "- Ask me about my experience, projects, or technical skills.\n"
    "- I will answer using only my indexed context."
)
UNABLE_TO_GENERATE_ANSWER = "TLDR: Unable to generate an answer.\nWrap: Try again shortly."
ENGLISH_INPUT_ONLY_ANSWER = (
    "TLDR: I support English input only right now.\n"
    "Wrap: Please rephrase your question in English."
)

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
GREETING_SINGLE_TOKENS: set[str] = {
    "hi",
    "hello",
    "hey",
    "yo",
    "hiya",
    "howdy",
    "sup",
    "wassup",
    "wassap",
}
GREETING_PHRASE_TOKENS: set[tuple[str, ...]] = {
    ("good", "morning"),
    ("good", "afternoon"),
    ("good", "evening"),
    ("what", "s", "up"),
    ("how", "are", "you"),
    ("how", "you", "doing"),
    ("how", "you", "doin"),
    ("how", "do", "you", "feel"),
}
GREETING_FILLER_TOKENS: set[str] = {
    "there",
    "team",
    "all",
    "everyone",
    "folks",
    "friend",
    "friends",
    "mate",
}
llm_gate_reason_SCORE_BELOW_THRESHOLD = "score_below"
llm_gate_reason_BM25_BELOW_THRESHOLD = "bm25_below"
llm_gate_reason_NO_CANDIDATES = "no_candidates"
llm_gate_reason_GREETING_BYPASS = "greeting_bypass"
llm_gate_reason_NON_ENGLISH_INPUT = "non_english_input"
llm_gate_reason_DURATION_BYPASS = "duration_bypass"
# Gating status label, not sensitive data.
llm_gate_reason_PASS = "pass"  # noqa: S105  # nosec B105
MIN_WEIGHTED_CONSENSUS_COUNT = DEFAULT_WEIGHTED_CONSENSUS_COUNT
RELATED_EXPERIENCE_MARKER = "but i do have related experience:"
BULLET_PREFIX = "- "
BULLET_TRANSITION_LINE = "More specifically:"
SPLIT_ON_CONNECTORS_PATTERN = re.compile(r"\s*(?:,|;|\band\b|/)\s*")
NON_ALPHANUMERIC_PATTERN = re.compile(r"[^a-z0-9\s]")
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_LATIN_LETTER_RATIO_THRESHOLD = 0.30


class RetrievalPipeline(Protocol):
    """Protocol for retrieval helpers used by the orchestrator."""

    def normalize_question_for_first_person(self, question: str) -> str: ...

    def embed_query(self, question: str) -> Optional[List[float]]: ...

    def search_vector_store(
        self, embedding: Optional[Sequence[float]], top_k: int
    ) -> List[Dict[str, Any]]: ...

    def apply_filters_and_boosting(
        self, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]: ...

    def has_selected_chunks(self, selected: List[Dict[str, Any]]) -> bool: ...

    def get_chunk_store_snapshot(self) -> Mapping[str, Mapping[str, Any]]: ...


@dataclass(frozen=True)
class ChatResult:
    """Orchestrated chat response with selected retrieval chunks."""

    response: ChatResponse
    selected_chunks: List[Dict[str, Any]]
    normalized_question: str
    usage_detail: "UsageDetail"
    thinking_budget_tokens_effective: int | None
    llm_gate_enabled: bool
    would_call_llm_if_gated: bool
    llm_gate_reason: str
    top1_weighted_score: float | None
    top1_bm25_score: float | None
    top1_vector_score: float | None
    best_weighted_score: float | None
    best_bm25_score: float | None
    weighted_consensus_count : int
    weighted_score_threshold: float
    bm25_score_threshold: float


class UsageDetail(TypedDict):
    """Backend-only usage detail from provider metadata.

    Fields are optional in provider responses, so values may be None.
    """

    total_tokens: int | None
    finish_reason: str | None


@dataclass(frozen=True)
class LlmGateShadowDecision:
    """Deterministic llm-gating shadow decision from top-ranked retrieval.

    Inputs:
    - would_call_llm: Whether threshold gating would call the LLM.
    - reason: Stable decision reason for logs and telemetry.
    - top1_weighted_score: Top candidates weighted score.
    - top1_bm25_score: Top candidate lexical BM25 score.
    - top1_vector_score: Top candidate vector similarity score.
    - best_weighted_score: Best weighted score across selected chunks.
    - best_bm25_score: Best BM25 score across selected chunks.
    - weighted_consensus_count : Number of chunks with weighted score meeting threshold.
    - weighted_score_threshold: Weighted score threshold used for evaluation.
    - bm25_score_threshold: BM25 threshold used for evaluation.

    Output:
    - Immutable decision payload consumed by chat orchestration and logs.

    Edge cases:
    - Top candidate scores may be None when fields are missing or non-numeric.

    Concurrency/atomicity:
    - Pure value object; safe for concurrent reads.
    """

    would_call_llm: bool
    reason: str
    top1_weighted_score: float | None
    top1_bm25_score: float | None
    top1_vector_score: float | None
    best_weighted_score: float | None
    best_bm25_score: float | None
    weighted_consensus_count : int
    weighted_score_threshold: float
    bm25_score_threshold: float


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
    enable_llm_call_gating: bool = False,
    weighted_score_threshold: float = DEFAULT_WEIGHTED_SCORE_THRESHOLD,
    bm25_score_threshold: float = DEFAULT_BM25_SCORE_THRESHOLD,
    weighted_consensus_count: int = MIN_WEIGHTED_CONSENSUS_COUNT,
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
    - enable_llm_call_gating: Whether deterministic retrieval llm gating is enabled.
    - weighted_score_threshold: Top-1 weighted score threshold for retrieval signal.
    - bm25_score_threshold: Top-1 BM25 threshold for retrieval signal.
    - weighted_consensus_count: Minimum number of chunks that must meet the
      weighted-score threshold for semantic signal to pass.

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
    if not _is_supported_english_input(normalized_question):
        english_only_usage = Usage(
            input_tokens=max(1, len(normalized_question) // APPROX_CHARS_PER_TOKEN),
            output_tokens=max(
                1, len(ENGLISH_INPUT_ONLY_ANSWER) // APPROX_CHARS_PER_TOKEN
            ),
        )
        return ChatResult(
            response=ChatResponse(
                answer=ENGLISH_INPUT_ONLY_ANSWER,
                citations=[],
                usage=english_only_usage,
                llm_called=False,
                input_token_limit=max_input_tokens,
            ),
            selected_chunks=[],
            normalized_question=normalized_question,
            usage_detail=_empty_usage_detail(),
            thinking_budget_tokens_effective=_resolve_thinking_budget_tokens(
                normalized_question,
                selected_chunks_count=0,
                enable_thinking_gating=enable_thinking_gating,
                default_thinking_budget_tokens=default_thinking_budget_tokens,
            ),
            llm_gate_enabled=enable_llm_call_gating,
            would_call_llm_if_gated=False,
            llm_gate_reason=llm_gate_reason_NON_ENGLISH_INPUT,
            top1_weighted_score=None,
            top1_bm25_score=None,
            top1_vector_score=None,
            best_weighted_score=None,
            best_bm25_score=None,
            weighted_consensus_count=0,
            weighted_score_threshold=float(weighted_score_threshold),
            bm25_score_threshold=float(bm25_score_threshold),
        )
    if _is_greeting_only_question(normalized_question):
        greeting_usage = Usage(
            input_tokens=max(1, len(normalized_question) // APPROX_CHARS_PER_TOKEN),
            output_tokens=max(1, len(GREETING_ONLY_ANSWER) // APPROX_CHARS_PER_TOKEN),
        )
        return ChatResult(
            response=ChatResponse(
                answer=GREETING_ONLY_ANSWER,
                citations=[],
                usage=greeting_usage,
                llm_called=False,
                input_token_limit=max_input_tokens,
            ),
            selected_chunks=[],
            normalized_question=normalized_question,
            usage_detail=_empty_usage_detail(),
            thinking_budget_tokens_effective=_resolve_thinking_budget_tokens(
                normalized_question,
                selected_chunks_count=0,
                enable_thinking_gating=enable_thinking_gating,
                default_thinking_budget_tokens=default_thinking_budget_tokens,
            ),
            llm_gate_enabled=enable_llm_call_gating,
            would_call_llm_if_gated=False,
            llm_gate_reason=llm_gate_reason_GREETING_BYPASS,
            top1_weighted_score=None,
            top1_bm25_score=None,
            top1_vector_score=None,
            best_weighted_score=None,
            best_bm25_score=None,
            weighted_consensus_count =0,
            weighted_score_threshold=float(weighted_score_threshold),
            bm25_score_threshold=float(bm25_score_threshold),
        )
    duration_routed_result = _route_duration_question_if_supported(
        normalized_question=normalized_question,
        retrieval=retrieval,
        max_input_tokens=max_input_tokens,
        enable_thinking_gating=enable_thinking_gating,
        default_thinking_budget_tokens=default_thinking_budget_tokens,
        enable_llm_call_gating=enable_llm_call_gating,
        weighted_score_threshold=weighted_score_threshold,
        bm25_score_threshold=bm25_score_threshold,
    )
    if duration_routed_result is not None:
        return duration_routed_result

    query_embedding = retrieval.embed_query(normalized_question)
    candidate_chunks = retrieval.search_vector_store(query_embedding, top_k=top_k)
    selected_chunks = retrieval.apply_filters_and_boosting(candidate_chunks)
    thinking_budget_tokens_effective = _resolve_thinking_budget_tokens(
        normalized_question,
        selected_chunks_count=len(selected_chunks),
        enable_thinking_gating=enable_thinking_gating,
        default_thinking_budget_tokens=default_thinking_budget_tokens,
    )
    signal_shadow_decision = compute_llm_gate_decision(
        selected_chunks,
        weighted_score_threshold=weighted_score_threshold,
        bm25_score_threshold=bm25_score_threshold,
        weighted_consensus_count=weighted_consensus_count,
        question_is_in_domain=retrieval.has_selected_chunks(selected_chunks),
    )
    would_call_llm = signal_shadow_decision.would_call_llm
    if not enable_llm_call_gating:
        would_call_llm = retrieval.has_selected_chunks(selected_chunks)

    if not would_call_llm:
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
                llm_called=False,
                input_token_limit=max_input_tokens,
            ),
            selected_chunks=[],
            normalized_question=normalized_question,
            usage_detail=_empty_usage_detail(),
            thinking_budget_tokens_effective=thinking_budget_tokens_effective,
            llm_gate_enabled=enable_llm_call_gating,
            would_call_llm_if_gated=signal_shadow_decision.would_call_llm,
            llm_gate_reason=signal_shadow_decision.reason,
            top1_weighted_score=signal_shadow_decision.top1_weighted_score,
            top1_bm25_score=signal_shadow_decision.top1_bm25_score,
            top1_vector_score=signal_shadow_decision.top1_vector_score,
            best_weighted_score=signal_shadow_decision.best_weighted_score,
            best_bm25_score=signal_shadow_decision.best_bm25_score,
            weighted_consensus_count =signal_shadow_decision.weighted_consensus_count ,
            weighted_score_threshold=signal_shadow_decision.weighted_score_threshold,
            bm25_score_threshold=signal_shadow_decision.bm25_score_threshold,
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
    answer_final = _deduplicate_answer_bullets(answer_text.strip())
    answer_final = _insert_transition_line_before_bullets(answer_final)
    if not answer_final:
        answer_final = UNABLE_TO_GENERATE_ANSWER

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
            llm_called=True,
            input_token_limit=max_input_tokens,
        ),
        selected_chunks=selected_chunks,
        normalized_question=normalized_question,
        usage_detail=usage_detail,
        thinking_budget_tokens_effective=thinking_budget_tokens_effective,
        llm_gate_enabled=enable_llm_call_gating,
        would_call_llm_if_gated=signal_shadow_decision.would_call_llm,
        llm_gate_reason=signal_shadow_decision.reason,
        top1_weighted_score=signal_shadow_decision.top1_weighted_score,
        top1_bm25_score=signal_shadow_decision.top1_bm25_score,
        top1_vector_score=signal_shadow_decision.top1_vector_score,
        best_weighted_score=signal_shadow_decision.best_weighted_score,
        best_bm25_score=signal_shadow_decision.best_bm25_score,
        weighted_consensus_count =signal_shadow_decision.weighted_consensus_count ,
        weighted_score_threshold=signal_shadow_decision.weighted_score_threshold,
        bm25_score_threshold=signal_shadow_decision.bm25_score_threshold,
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


def _deduplicate_answer_bullets(answer_text: str) -> str:
    """Remove bullets that repeat facts already present in the first sentence.

    Inputs:
    - answer_text: Raw LLM answer that may include one sentence and optional bullets.

    Output:
    - Cleaned answer text that preserves order but removes redundant bullets.

    Edge cases:
    - Empty input returns an empty string.
    - Single-line answers are returned unchanged.
    - Non-bullet continuation lines are preserved as-is.

    Concurrency/atomicity:
    - Pure string transformation with no shared state mutation.
    """
    stripped_answer = (answer_text or "").strip()
    if not stripped_answer:
        return ""

    lines = stripped_answer.splitlines()
    if len(lines) <= 1:
        return stripped_answer

    first_line = lines[0].strip()
    if not first_line:
        return stripped_answer

    dedupe_targets = _build_bullet_dedupe_targets(first_line)
    if not dedupe_targets:
        return stripped_answer

    cleaned_lines = [lines[0]]
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line.startswith(BULLET_PREFIX):
            cleaned_lines.append(raw_line)
            continue

        bullet_text = line[len(BULLET_PREFIX) :].strip()
        normalized_bullet_text = _normalize_answer_text_for_dedupe(bullet_text)
        if not normalized_bullet_text:
            continue
        if normalized_bullet_text in dedupe_targets:
            continue
        cleaned_lines.append(raw_line)

    return "\n".join(cleaned_lines).strip()


def _insert_transition_line_before_bullets(answer_text: str) -> str:
    """Insert a short transition line before the first bullet when bullets exist.

    Inputs:
    - answer_text: Raw or post-processed answer text that may include bullets.

    Output:
    - Answer text with a connective line before the bullet list.

    Edge cases:
    - Empty input returns an empty string.
    - Single-line answers are returned unchanged.
    - Existing transition lines are preserved (no duplicate insertion).
    - Other non-bullet lines between the first sentence and the first bullet are removed.
    """
    stripped_answer = (answer_text or "").strip()
    if not stripped_answer:
        return ""

    lines = stripped_answer.splitlines()
    if len(lines) <= 1:
        return stripped_answer

    first_bullet_index = -1
    for line_index, raw_line in enumerate(lines[1:], start=1):
        if raw_line.strip().startswith(BULLET_PREFIX):
            first_bullet_index = line_index
            break

    if first_bullet_index <= 0:
        return stripped_answer

    # Normalize any model-emitted "bridge" lines so the transition is consistent.
    # Contract: only one line may appear between the first sentence and the first bullet.
    transition_line_lower = BULLET_TRANSITION_LINE.lower()
    non_bullet_indices_between = [
        line_index
        for line_index in range(1, first_bullet_index)
        if lines[line_index].strip()
        and not lines[line_index].strip().startswith(BULLET_PREFIX)
    ]
    transition_indices_between = [
        line_index
        for line_index in non_bullet_indices_between
        if lines[line_index].strip().lower() == transition_line_lower
    ]

    if transition_indices_between:
        # Keep the last canonical transition line and remove other non-bullet lines
        # (including earlier transition duplicates) between sentence and bullets.
        transition_index_to_keep = transition_indices_between[-1]
        for line_index in reversed(non_bullet_indices_between):
            if line_index != transition_index_to_keep:
                lines.pop(line_index)
        return "\n".join(lines).strip()

    for line_index in reversed(non_bullet_indices_between):
        lines.pop(line_index)
        first_bullet_index -= 1

    lines.insert(first_bullet_index, BULLET_TRANSITION_LINE)
    return "\n".join(lines).strip()


def _build_bullet_dedupe_targets(first_line: str) -> set[str]:
    """Build normalized text targets that bullets should not repeat.

    Inputs:
    - first_line: The first line of the generated answer.

    Output:
    - Set of normalized text variants that represent first-line facts.

    Edge cases:
    - Empty or non-normalizable first lines return an empty set.
    - Related-experience fragments are extracted only when the marker exists.
    """
    normalized_first_line = _normalize_answer_text_for_dedupe(first_line)
    if not normalized_first_line:
        return set()

    dedupe_targets = {normalized_first_line}
    lowered_first_line = first_line.lower()
    marker_index = lowered_first_line.find(RELATED_EXPERIENCE_MARKER)
    if marker_index < 0:
        return dedupe_targets

    related_experience_start = marker_index + len(RELATED_EXPERIENCE_MARKER)
    related_experience_text = first_line[related_experience_start:].strip()
    normalized_related_experience_text = _normalize_answer_text_for_dedupe(
        related_experience_text
    )
    if normalized_related_experience_text:
        dedupe_targets.add(normalized_related_experience_text)

    for fragment in SPLIT_ON_CONNECTORS_PATTERN.split(related_experience_text):
        normalized_fragment = _normalize_answer_text_for_dedupe(fragment)
        if normalized_fragment:
            dedupe_targets.add(normalized_fragment)

    return dedupe_targets


def _normalize_answer_text_for_dedupe(text: str) -> str:
    """Normalize answer text for deterministic duplicate detection.

    Inputs:
    - text: Arbitrary answer fragment.

    Output:
    - Lowercased alphanumeric string with collapsed whitespace.

    Edge cases:
    - Empty text or punctuation-only text returns an empty string.
    """
    lowered_text = (text or "").strip().lower()
    if not lowered_text:
        return ""
    text_without_punctuation = NON_ALPHANUMERIC_PATTERN.sub(" ", lowered_text)
    normalized_text = WHITESPACE_PATTERN.sub(" ", text_without_punctuation).strip()
    return normalized_text


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


def _is_supported_english_input(question: str) -> bool:
    """Check whether a question is likely English-script input.

    Inputs:
    - question: Raw or normalized user text.

    Output:
    - True when the text is empty/punctuation-only, or when most letters belong
      to the Latin script.

    Edge cases:
    - Empty input is treated as supported to preserve existing no-signal flow.
    - Questions with mixed scripts are rejected only when non-Latin letters are
      at or above the configured ratio threshold.

    Concurrency/atomicity:
    - Pure text classification with no shared state mutation.
    """
    normalized_question = (question or "").strip()
    if not normalized_question:
        return True

    total_letter_count = 0
    non_latin_letter_count = 0
    for character in normalized_question:
        if not character.isalpha():
            continue

        total_letter_count += 1
        try:
            unicode_name = unicodedata.name(character)
        except ValueError:
            non_latin_letter_count += 1
            continue

        if "LATIN" not in unicode_name:
            non_latin_letter_count += 1

    if total_letter_count == 0:
        return True

    non_latin_letter_ratio = non_latin_letter_count / total_letter_count
    return non_latin_letter_ratio < NON_LATIN_LETTER_RATIO_THRESHOLD


def _is_greeting_only_question(question: str) -> bool:
    """Determine whether the input is greeting-only with no substantive ask.

    Inputs:
    - question: Raw user input text.

    Output:
    - True when the text contains one or more greeting tokens and any remaining
      tokens are greeting fillers only.

    Edge cases:
    - Empty or punctuation-only inputs return False.
    - Multiword greetings such as "good morning" are recognized.
    - Inputs that append a real request after a greeting return False.

    Concurrency/atomicity:
    - Pure text classification with no shared state mutation.
    """
    normalized_question = _normalize_answer_text_for_dedupe(question)
    if not normalized_question:
        return False

    tokens = normalized_question.split()
    if not tokens:
        return False

    greeting_phrases_by_length: dict[int, set[tuple[str, ...]]] = {}
    for phrase_tokens in GREETING_PHRASE_TOKENS:
        phrase_length = len(phrase_tokens)
        if phrase_length not in greeting_phrases_by_length:
            greeting_phrases_by_length[phrase_length] = set()
        greeting_phrases_by_length[phrase_length].add(phrase_tokens)
    supported_phrase_lengths = sorted(greeting_phrases_by_length.keys(), reverse=True)

    consumed_greeting_tokens = 0
    token_index = 0
    while token_index < len(tokens):
        matched_phrase_length = 0
        for phrase_length in supported_phrase_lengths:
            phrase_end_index = token_index + phrase_length
            if phrase_end_index > len(tokens):
                continue
            candidate_phrase = tuple(tokens[token_index:phrase_end_index])
            if candidate_phrase in greeting_phrases_by_length[phrase_length]:
                matched_phrase_length = phrase_length
                break
        if matched_phrase_length > 0:
            consumed_greeting_tokens += matched_phrase_length
            token_index += matched_phrase_length
            continue
        if tokens[token_index] in GREETING_SINGLE_TOKENS:
            consumed_greeting_tokens += 1
            token_index += 1
            continue
        break

    if consumed_greeting_tokens == 0:
        return False

    remaining_tokens = tokens[token_index:]
    if not remaining_tokens:
        return True
    return all(token in GREETING_FILLER_TOKENS for token in remaining_tokens)


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
    chunk_id = str(chunk.get("chunk_id") or "")
    text = str(chunk.get("text") or "").strip()
    snippet = " ".join(text.split())
    if snippet and len(snippet) > SNIPPET_CHAR_LIMIT:
        max_snippet_length = SNIPPET_CHAR_LIMIT - len(ELLIPSIS_SUFFIX)
        snippet = snippet[:max_snippet_length].rstrip() + ELLIPSIS_SUFFIX
    return Citation(id=chunk_id, text=snippet or None)


def compute_llm_gate_decision(
    selected_chunks: List[Dict[str, Any]],
    *,
    weighted_score_threshold: float,
    bm25_score_threshold: float,
    weighted_consensus_count: int = MIN_WEIGHTED_CONSENSUS_COUNT,
    question_is_in_domain: bool | None = None,
) -> LlmGateShadowDecision:
    """Return the deterministic llm gating decision used by chat orchestration.

    Inputs:
    - selected_chunks: Ranked retrieval chunks from filtering and boosting.
    - weighted_score_threshold: Minimum acceptable weighted score.
    - bm25_score_threshold: Minimum acceptable BM25 score.
    - weighted_consensus_count: Minimum number of chunks that must meet the
      weighted-score threshold for semantic signal to pass.
    - question_is_in_domain: Optional in-domain flag for BM25 fallback checks.

    Output:
    - LlmGateShadowDecision with would-call verdict, reason, top-1 metrics,
      and threshold values.

    Edge cases:
    - Empty selections return would_call_llm=False with `no_candidates`.
    - Missing/non-numeric score fields are treated as absent and fail thresholds.

    Concurrency/atomicity:
    - Pure computation with no shared state mutation.
    """

    return _compute_llm_gate_decision(
        selected_chunks,
        weighted_score_threshold=weighted_score_threshold,
        bm25_score_threshold=bm25_score_threshold,
        weighted_consensus_count=weighted_consensus_count,
        question_is_in_domain=question_is_in_domain,
    )


def _route_duration_question_if_supported(
    *,
    normalized_question: str,
    retrieval: RetrievalPipeline,
    max_input_tokens: Optional[int],
    enable_thinking_gating: bool,
    default_thinking_budget_tokens: int | None,
    enable_llm_call_gating: bool,
    weighted_score_threshold: float,
    bm25_score_threshold: float,
) -> ChatResult | None:
    """Route duration questions to deterministic metadata-only answers.

    Inputs:
    - normalized_question: Already-normalized user question text.
    - retrieval: Retrieval pipeline object, optionally exposing chunk snapshot access.
    - max_input_tokens: Input token limit echoed in the ChatResponse.
    - enable_thinking_gating: Flag for deterministic thinking-budget gating.
    - default_thinking_budget_tokens: Configured default thinking budget.
    - enable_llm_call_gating: Flag indicating whether LLM-call gating is enabled.
    - weighted_score_threshold: Weighted-score threshold used for telemetry fields.
    - bm25_score_threshold: BM25 threshold used for telemetry fields.

    Output:
    - ChatResult when duration routing applies; otherwise None to continue normal flow.

    Edge cases:
    - Returns None when question is not duration intent or chunk snapshot access
      is unavailable.
    """

    if not deterministic_duration.is_duration_intent(normalized_question):
        return None

    snapshot_accessor = getattr(retrieval, "get_chunk_store_snapshot", None)
    if not callable(snapshot_accessor):
        return None

    chunk_store_snapshot = snapshot_accessor()
    current_year = datetime.now(timezone.utc).year
    duration_result = deterministic_duration.compute_duration_for_question(
        chunk_store_snapshot,
        question=normalized_question,
        current_year=current_year,
    )
    answer_text = deterministic_duration.format_duration_answer(duration_result)
    if duration_result.union_matched_stints:
        based_on_text = deterministic_duration.format_based_on_stints(
            duration_result.union_matched_stints
        )
        answer_text = f"{answer_text}\n- {based_on_text}"
    usage = Usage(
        input_tokens=max(1, len(normalized_question) // APPROX_CHARS_PER_TOKEN),
        output_tokens=max(1, len(answer_text) // APPROX_CHARS_PER_TOKEN),
    )
    return ChatResult(
        response=ChatResponse(
            answer=answer_text,
            citations=[],
            usage=usage,
            llm_called=False,
            input_token_limit=max_input_tokens,
        ),
        selected_chunks=[],
        normalized_question=normalized_question,
        usage_detail=_empty_usage_detail(),
        thinking_budget_tokens_effective=_resolve_thinking_budget_tokens(
            normalized_question,
            selected_chunks_count=0,
            enable_thinking_gating=enable_thinking_gating,
            default_thinking_budget_tokens=default_thinking_budget_tokens,
        ),
        llm_gate_enabled=enable_llm_call_gating,
        would_call_llm_if_gated=False,
        llm_gate_reason=llm_gate_reason_DURATION_BYPASS,
        top1_weighted_score=None,
        top1_bm25_score=None,
        top1_vector_score=None,
        best_weighted_score=None,
        best_bm25_score=None,
        weighted_consensus_count=0,
        weighted_score_threshold=float(weighted_score_threshold),
        bm25_score_threshold=float(bm25_score_threshold),
    )


def _compute_llm_gate_decision(
    selected_chunks: List[Dict[str, Any]],
    *,
    weighted_score_threshold: float,
    bm25_score_threshold: float,
    weighted_consensus_count: int = MIN_WEIGHTED_CONSENSUS_COUNT,
    question_is_in_domain: bool | None = None,
) -> LlmGateShadowDecision:
    """Compute deterministic llm-gating decision without applying it.

    Inputs:
    - selected_chunks: Ranked retrieval chunks from filtering and boosting.
    - weighted_score_threshold: Minimum acceptable weighted score.
    - bm25_score_threshold: Minimum acceptable BM25 score.
    - weighted_consensus_count: Minimum number of chunks that must meet the
      weighted-score threshold for semantic signal to pass.
    - question_is_in_domain: Optional in-domain flag for BM25 fallback checks.

    Output:
    - LlmGateShadowDecision with would-call verdict, reason, top-1 score
      metadata, and threshold values used.

    Edge cases:
    - Empty selections return would_call_llm=False with `no_candidates`.
    - Missing/non-numeric score fields are treated as absent and fail thresholds.

    Concurrency/atomicity:
    - Pure computation with no shared state mutations.
    """
    normalized_weighted_score_threshold = float(weighted_score_threshold)
    normalized_bm25_score_threshold = float(bm25_score_threshold)
    normalized_weighted_consensus_count = max(1, int(weighted_consensus_count))
    resolved_question_is_in_domain = (
        bool(selected_chunks)
        if question_is_in_domain is None
        else bool(question_is_in_domain)
    )
    top_chunk = selected_chunks[0] if selected_chunks else {}
    top1_weighted_score = _optional_float(top_chunk.get("score"))
    top1_bm25_score = _optional_float(top_chunk.get("bm25_score"))
    top1_vector_score = _optional_float(top_chunk.get("vector_score"))

    if not selected_chunks:
        return LlmGateShadowDecision(
            would_call_llm=False,
            reason=llm_gate_reason_NO_CANDIDATES,
            top1_weighted_score=None,
            top1_bm25_score=None,
            top1_vector_score=None,
            best_weighted_score=None,
            best_bm25_score=None,
            weighted_consensus_count =0,
            weighted_score_threshold=normalized_weighted_score_threshold,
            bm25_score_threshold=normalized_bm25_score_threshold,
        )

    weighted_scores = [
        weighted_score
        for chunk in selected_chunks
        if (weighted_score := _optional_float(chunk.get("score"))) is not None
    ]
    bm25_scores = [
        bm25_score
        for chunk in selected_chunks
        if (bm25_score := _optional_float(chunk.get("bm25_score"))) is not None
    ]
    best_weighted_score = max(weighted_scores) if weighted_scores else None
    best_bm25_score = max(bm25_scores) if bm25_scores else None
    support_count = sum(
        1
        for weighted_score in weighted_scores
        if weighted_score >= normalized_weighted_score_threshold
    )
    passes_semantic_signal = (
        best_weighted_score is not None
        and best_weighted_score >= normalized_weighted_score_threshold
        and support_count >= normalized_weighted_consensus_count
    )
    passes_bm25_fallback_signal = (
        best_bm25_score is not None
        and best_bm25_score >= normalized_bm25_score_threshold
        and resolved_question_is_in_domain
    )
    would_call_llm = passes_semantic_signal or passes_bm25_fallback_signal
    reason = (
        llm_gate_reason_PASS
        if would_call_llm
        else llm_gate_reason_SCORE_BELOW_THRESHOLD
    )

    return LlmGateShadowDecision(
        would_call_llm=would_call_llm,
        reason=reason,
        top1_weighted_score=top1_weighted_score,
        top1_bm25_score=top1_bm25_score,
        top1_vector_score=top1_vector_score,
        best_weighted_score=best_weighted_score,
        best_bm25_score=best_bm25_score,
        weighted_consensus_count =support_count,
        weighted_score_threshold=normalized_weighted_score_threshold,
        bm25_score_threshold=normalized_bm25_score_threshold,
    )


def _optional_float(value: Any) -> float | None:
    """Convert an arbitrary value to float when possible.

    Inputs:
    - value: Any candidate value that may represent a numeric score.

    Output:
    - Float value when conversion succeeds; otherwise None.

    Edge cases:
    - None, non-numeric strings, and unsupported types return None.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
