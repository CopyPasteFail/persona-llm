"""Tests normalization of persona-name references into first-person language."""

import itertools

import pytest

from api.retrieval import normalize_question_for_first_person as normalize_question
from api.settings import settings

PERSONA_NAME = settings.PERSONA_NAME.strip()
PERSONA_NAME_PARTS = PERSONA_NAME.split()
PERSONA_FIRST_NAME = PERSONA_NAME_PARTS[0]
PERSONA_LAST_NAME = PERSONA_NAME_PARTS[-1]
PERSONA_FIRST_NAME_LOWER = PERSONA_FIRST_NAME.lower()
PERSONA_LAST_NAME_LOWER = PERSONA_LAST_NAME.lower()


def build_persona_name_variants() -> list[str]:
    """
    All permutations of all non-empty subsets of the persona name parts.
    For N parts (N <= 4), this yields sum_{r=1..N} P(N, r) variants.
    """
    variants: list[str] = []
    name_part_count = len(PERSONA_NAME_PARTS)
    for subset_size in range(1, name_part_count + 1):
        for name_parts in itertools.permutations(PERSONA_NAME_PARTS, subset_size):
            variants.append(" ".join(name_parts))
    # De-dupe while preserving order.
    seen_variants: set[str] = set()
    unique_variants: list[str] = []
    for variant in variants:
        if variant not in seen_variants:
            seen_variants.add(variant)
            unique_variants.append(variant)
    return unique_variants


def inputs_that_should_not_change() -> list[str]:
    """
    Cases that MUST NOT change:
      - emails (localpart equals first/last)
      - URLs/handles (path equals first/last)
      - inside-word substring (e.g., First + 'on')
      - filesystem-ish path segment (/u/<first>/repos)
      - glued possessives without boundary after 's (First’sTeam / First'sTeam)
    """
    cases = {
        f"Contact: {PERSONA_FIRST_NAME_LOWER}@company.com",
        f"Profile: https://x.com/{PERSONA_FIRST_NAME_LOWER}",
        f"We use {PERSONA_FIRST_NAME}on devices",  # inside-word, no boundary
        f"Path: /u/{PERSONA_FIRST_NAME_LOWER}/repos",
        f"{PERSONA_FIRST_NAME}’sTeam did this",  # glued possessive with curly apos
        f"{PERSONA_FIRST_NAME}'sTeam did this",  # glued possessive with straight apos
    }
    # If last token differs from first, add last-specific variants.
    if PERSONA_LAST_NAME_LOWER != PERSONA_FIRST_NAME_LOWER:
        cases.update({
            f"Contact: {PERSONA_LAST_NAME_LOWER}@company.com",
            f"Profile: https://x.com/{PERSONA_LAST_NAME_LOWER}",
        })
    # Return in stable order.
    return sorted(cases)


# Curly apostrophe cases (’)
@pytest.mark.parametrize("variant", build_persona_name_variants())
def test_curly_apos_with_punct(variant: str) -> None:
    assert normalize_question(f"{variant}’s.") == "your."
    assert normalize_question(f"{variant}’s,") == "your,"
    assert normalize_question(f"{variant}’s?") == "your?"
    assert normalize_question(f"{variant}’s!") == "your!"
    assert normalize_question(f"{variant}’s;") == "your;"
    assert normalize_question(f"{variant}’s:") == "your:"
    assert normalize_question(f"Tell me about {variant}’s.") == "Tell me about your."


# Straight apostrophe cases (')
@pytest.mark.parametrize("variant", build_persona_name_variants())
def test_straight_apos_with_punct(variant: str) -> None:
    assert normalize_question(f"{variant}'s.") == "your."
    assert normalize_question(f"{variant}'s,") == "your,"
    assert normalize_question(f"{variant}'s?") == "your?"
    assert normalize_question(f"{variant}'s!") == "your!"
    assert normalize_question(f"{variant}'s;") == "your;"
    assert normalize_question(f"{variant}'s:") == "your:"
    assert normalize_question(f"Tell me about {variant}'s.") == "Tell me about your."


# Bare name used as subject/object becomes "I"
@pytest.mark.parametrize("variant", build_persona_name_variants())
def test_bare_name_subject_object(variant: str) -> None:
    assert normalize_question(f"Tell me about {variant}.") == "Tell me about I."
    assert normalize_question(f"What did {variant} do?") == "What did I do?"


# Should NOT change (dynamic)
@pytest.mark.parametrize("input_text", inputs_that_should_not_change())
def test_should_not_change(input_text: str) -> None:
    assert normalize_question(input_text) == input_text
