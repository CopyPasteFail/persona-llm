"""
Retrieval helpers. Keep pure and side-effect free.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# Apostrophe: support curly and straight
_APOS = r"[’']"

# 1) Possessive: ("Omer's" | "Omer Reznik's") -> "your"
#    - Require a word boundary after the 's' so "Omer'sTeam" is NOT matched here.
#    - Skip if adjacent to emails/usernames.
_POSSESSIVE = re.compile(
    rf"(?<![\w.+-/])(Omer(?:\s+Reznik)?){_APOS}s\b(?!@)", re.IGNORECASE | re.UNICODE
)

# 2) Name to first-person:
#    "Omer Reznik" / "Omer" -> "I"
#    Guardrails:
#      - leading negative LB to avoid emails/handles/paths (@, /, word char)
#      - trailing \b so we don't match inside words (e.g., "Omeron")
#      - also DO NOT match if immediately followed by "'s" + word with no boundary
#        (e.g., "Omer'sTeam"), which we want to leave untouched.
_NAME_SUBJECT = re.compile(
    rf"(?<![@/\w])(Omer(?:\s+Reznik)?)\b(?!{_APOS}s\w)(?!@)",
    re.IGNORECASE | re.UNICODE,
)

def normalize_question_for_first_person(q: str) -> str:
    """
    Convert obvious third-person mentions of Omer to first-person language for consistency.
    Keep emails/URLs/usernames untouched. Avoid changing glued possessives and inside-word matches.
    """
    if not q:
        return q
    # possessive -> your
    out = _POSSESSIVE.sub("your", q)

    # subject/object -> I
    out = _NAME_SUBJECT.sub("I", out)

    return out

# ---- Real integrations (unimplemented in mock) ----

def embed_query(question: str):
    raise NotImplementedError("embed_query not implemented")

def search_vector_store(embedding, top_k: int = 8):
    raise NotImplementedError("search_vector_store not implemented")

def apply_filters_and_boosting(candidates: List[Dict[str, Any]]):
    """
    New signature: no filters. Keep all candidates, maybe re-rank lightly.
    """
    raise NotImplementedError("apply_filters_and_boosting not implemented")

def build_context_prompt(question: str, selected: List[Dict[str, Any]]) -> str:
    raise NotImplementedError("build_context_prompt not implemented")

def has_signal(selected: List[Dict[str, Any]]) -> bool:
    """Helper used by main.py mock/real path to decide if we answer at all."""
    return bool(selected)
