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

from api.llm import build_llm_prompt, _estimate_tokens, _trim_chunks_to_budget


def test_build_llm_prompt_contract_includes_persona_and_context():
    """Ensure the prompt payload includes persona name and context chunks."""
    chunks = [
        {"id": "c1", "text": "Worked on distributed systems."},
        {"id": "c2", "text": "Shipped data pipelines."},
    ]

    payload = build_llm_prompt(
        "What did you build?",
        chunks,
        persona_name="Avery",
        max_input_tokens=500,
    )

    assert isinstance(payload, dict)
    assert "messages" in payload
    assert len(payload["messages"]) == 2

    system = payload["messages"][0]["content"]
    user = payload["messages"][1]["content"]

    assert "You are Avery speaking in first person." in system
    assert "Question: What did you build?" in user
    assert "[1] Worked on distributed systems." in user
    assert "[2] Shipped data pipelines." in user
    assert "Only use facts that appear in Context." in user


def test_trim_chunks_to_budget_truncates_first_chunk_when_tight():
    """Verify tight budgets truncate the first chunk to remaining capacity."""
    system_prompt = "S"
    question = "Q"
    chunks = [
        {"id": "c1", "text": "A" * 100},
        {"id": "c2", "text": "B" * 100},
    ]

    base_user = (
        f"Question: {question}\n\nContext:\n\nOnly use facts that appear in Context."
    )
    base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(base_user)
    max_input_tokens = base_tokens + 1

    trimmed = _trim_chunks_to_budget(
        chunks,
        max_input_tokens=max_input_tokens,
        system_prompt=system_prompt,
        question=question,
    )

    assert len(trimmed) == 1
    assert trimmed[0]["id"] == "c1"
    assert trimmed[0]["text"] == "A" * 4


def test_trim_chunks_to_budget_keeps_first_full_chunk_when_budget_exact():
    """Confirm exact budgets allow the first full chunk without truncation."""
    system_prompt = "S"
    question = "Q"
    chunks = [
        {"id": "c1", "text": "A" * 20},
        {"id": "c2", "text": "B" * 20},
    ]

    base_user = (
        f"Question: {question}\n\nContext:\n\nOnly use facts that appear in Context."
    )
    base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(base_user)
    first_block_tokens = _estimate_tokens(f"[1] {chunks[0]['text']}")
    max_input_tokens = base_tokens + first_block_tokens

    trimmed = _trim_chunks_to_budget(
        chunks,
        max_input_tokens=max_input_tokens,
        system_prompt=system_prompt,
        question=question,
    )

    assert len(trimmed) == 1
    assert trimmed[0]["text"] == chunks[0]["text"]
