"""
Retrieval helpers. Keep pure and side-effect free.

Name normalization uses the configured persona name from settings.PERSONA_NAME.
It generates regexes for *all permutations* of up to 4 name parts. For example:
  "Alex Taylor" -> ["Alex", "Taylor", "Alex Taylor", "Taylor Alex"]

To avoid the issue where regexes were compiled before the environment was
loaded (tests failed because PERSONA_NAME wasn’t set yet), regexes are now
compiled lazily each time normalize_question_for_first_person is called.
"""
from __future__ import annotations

import re
import itertools
from typing import Any, Dict, List

from .settings import settings

# Apostrophe: support curly and straight
_APOS = r"[’']"


def _persona_variants() -> list[str]:
    """
    Generate all permutations of all non-empty subsets of the persona name parts.
    Example: "Alex Taylor" -> ["Alex", "Taylor", "Alex Taylor", "Taylor Alex"].
    Enforced max 4 words in settings prevents combinatorial explosion.
    """
    full = (settings.PERSONA_NAME or "").strip()
    if not full:
        return []

    parts = full.split()
    variants: list[str] = []
    n = len(parts)
    for r in range(1, n + 1):  # subset size
        for combo in itertools.permutations(parts, r):
            variants.append(" ".join(combo))

    # de-dupe while preserving order
    seen = set()
    uniq: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _compile_regexes():
    """
    Build regexes on demand using the current PERSONA_NAME from settings.
    Returns (possessive_regex, bare_regex).
    """
    variants = _persona_variants()
    if not variants:
        return None, None

    # IMPORTANT: longest-first so "Alex Taylor" beats "Alex" then "Taylor"
    variants = sorted(variants, key=len, reverse=True)

    alt = "|".join(re.escape(v) for v in variants)
    group = fr"(?:{alt})"

    possessive = re.compile(
        rf"(?<![\w.+-/]){group}{_APOS}s\b(?!@)",
        re.IGNORECASE | re.UNICODE,
    )

    bare = re.compile(
        rf"(?<![@/\w]){group}\b(?!{_APOS}s\w)(?!@)",
        re.IGNORECASE | re.UNICODE,
    )

    return possessive, bare


def normalize_question_for_first_person(q: str) -> str:
    """
    Convert references like "<Name>'s" -> "your" and "<Name>" -> "I",
    while avoiding emails, usernames and inside-word matches.

    Regexes are compiled lazily from settings.PERSONA_NAME so tests
    can inject env vars without being broken by early imports.
    """
    if not q:
        return q

    possessive, bare = _compile_regexes()
    if not possessive or not bare:
        return q

    out = possessive.sub("your", q)
    out = bare.sub("I", out)
    return out


# ---- Real integrations (unimplemented in mock) ----

def embed_query(question: str):
    raise NotImplementedError("embed_query not implemented")

def search_vector_store(embedding, top_k: int = 8):
    raise NotImplementedError("search_vector_store not implemented")

def apply_filters_and_boosting(candidates: List[Dict[str, Any]]):
    """Keep all candidates, maybe re-rank lightly."""
    raise NotImplementedError("apply_filters_and_boosting not implemented")

def build_context_prompt(question: str, selected: List[Dict[str, Any]]) -> str:
    raise NotImplementedError("build_context_prompt not implemented")

def has_signal(selected: List[Dict[str, Any]]) -> bool:
    """Helper used by main.py mock/real path to decide if we answer at all."""
    return bool(selected)
