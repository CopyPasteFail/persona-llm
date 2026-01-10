"""Tests for LLM prompt construction and token-budget trimming behavior.

Test cases covered:
- build_llm_prompt: given a question, two chunks, and a persona name, the output
  payload must contain exactly two messages because the contract builds one
  system message and one user message. The system message is expected to include
  the provided persona name verbatim because the function interpolates it into
  the system prompt. The user message is expected to include the input question,
  enumerated chunk text, and the fixed "Only use facts that appear in Context."
  instruction because the function formats these inputs directly into the user
  prompt string.
- _trim_chunks_to_budget (tight budget): given a max_input_tokens value that
  leaves only one token of remaining budget after the base prompt, the output is
  expected to include only the first chunk and to truncate its text to 4
  characters because the estimator treats one token as roughly four characters
  and the function truncates the first chunk when it cannot fully fit.
- _trim_chunks_to_budget (exact budget): given a max_input_tokens value that
  equals the base prompt tokens plus exactly the estimated tokens for the first
  chunk block, the output is expected to include the first chunk in full and to
  exclude subsequent chunks because the remaining budget reaches zero after the
  first block and no more chunks can fit.
"""

from typing import TypedDict, cast

from api.llm import (
    _estimate_tokens, # pyright: ignore[reportPrivateUsage]
    _trim_chunks_to_budget, # pyright: ignore[reportPrivateUsage]
    build_llm_prompt,
)

class PromptMessage(TypedDict):
    role: str
    content: str


class PromptPayload(TypedDict):
    messages: list[PromptMessage]

BASE_INSTRUCTION = "Only use facts that appear in Context."
PERSONA_NAME = "Avery"
QUESTION_TEXT = "What did you build?"
SYSTEM_PROMPT_PLACEHOLDER = "S"
QUESTION_PLACEHOLDER = "Q"
FIRST_CHUNK_ID = "c1"
SECOND_CHUNK_ID = "c2"
LONG_CHUNK_LENGTH = 100
SHORT_CHUNK_LENGTH = 20
ESTIMATED_CHARS_PER_TOKEN = 4


def test_build_llm_prompt_contract_includes_persona_and_context():
    """Ensure the prompt payload includes persona name and context chunks."""
    context_chunks: list[dict[str, str]] = [
        {"id": FIRST_CHUNK_ID, "text": "Worked on distributed systems."},
        {"id": SECOND_CHUNK_ID, "text": "Shipped data pipelines."},
    ]

    payload = cast(
        PromptPayload,
        build_llm_prompt(
            QUESTION_TEXT,
            context_chunks,
            persona_name=PERSONA_NAME,
            max_input_tokens=500,
        ),
    )

    assert isinstance(payload, dict)
    assert "messages" in payload
    assert len(payload["messages"]) == 2

    system_message = payload["messages"][0]["content"]
    user_message = payload["messages"][1]["content"]

    assert "You are Avery speaking in first person." in system_message
    assert "Question: What did you build?" in user_message
    assert "[1] Worked on distributed systems." in user_message
    assert "[2] Shipped data pipelines." in user_message
    assert BASE_INSTRUCTION in user_message


def test_trim_chunks_to_budget_truncates_first_chunk_when_tight():
    """Verify tight budgets truncate the first chunk to remaining capacity."""
    system_prompt = SYSTEM_PROMPT_PLACEHOLDER
    question = QUESTION_PLACEHOLDER
    context_chunks: list[dict[str, str]] = [
        {"id": FIRST_CHUNK_ID, "text": "A" * LONG_CHUNK_LENGTH},
        {"id": SECOND_CHUNK_ID, "text": "B" * LONG_CHUNK_LENGTH},
    ]

    base_user = (
        f"Question: {question}\n\nContext:\n\n{BASE_INSTRUCTION}"
    )
    base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(base_user)
    max_input_tokens = base_tokens + 1

    trimmed = cast(
        list[dict[str, str]],
        _trim_chunks_to_budget(
            context_chunks,
            max_input_tokens=max_input_tokens,
            system_prompt=system_prompt,
            question=question,
        ),
    )

    assert len(trimmed) == 1
    assert trimmed[0]["id"] == FIRST_CHUNK_ID
    assert trimmed[0]["text"] == "A" * ESTIMATED_CHARS_PER_TOKEN


def test_trim_chunks_to_budget_keeps_first_full_chunk_when_budget_exact():
    """Confirm exact budgets allow the first full chunk without truncation."""
    system_prompt = SYSTEM_PROMPT_PLACEHOLDER
    question = QUESTION_PLACEHOLDER
    context_chunks: list[dict[str, str]] = [
        {"id": FIRST_CHUNK_ID, "text": "A" * SHORT_CHUNK_LENGTH},
        {"id": SECOND_CHUNK_ID, "text": "B" * SHORT_CHUNK_LENGTH},
    ]

    base_user = (
        f"Question: {question}\n\nContext:\n\n{BASE_INSTRUCTION}"
    )
    base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(base_user)
    first_block_tokens = _estimate_tokens(f"[1] {context_chunks[0]['text']}")
    max_input_tokens = base_tokens + first_block_tokens

    trimmed = cast(
        list[dict[str, str]],
        _trim_chunks_to_budget(
            context_chunks,
            max_input_tokens=max_input_tokens,
            system_prompt=system_prompt,
            question=question,
        ),
    )

    assert len(trimmed) == 1
    assert trimmed[0]["text"] == context_chunks[0]["text"]
