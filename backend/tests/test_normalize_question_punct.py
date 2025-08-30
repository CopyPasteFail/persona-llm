import itertools
import pytest
from api.retrieval import normalize_question_for_first_person as norm
from api.settings import settings

NAME = settings.PERSONA_NAME.strip()
PARTS = NAME.split()
FIRST = PARTS[0]
LAST = PARTS[-1]
FIRST_L = FIRST.lower()
LAST_L = LAST.lower()


def all_variants():
    """
    All permutations of all non-empty subsets of the persona name parts.
    For N parts (N <= 4), this yields sum_{r=1..N} P(N, r) variants.
    """
    variants = []
    n = len(PARTS)
    for r in range(1, n + 1):
        for combo in itertools.permutations(PARTS, r):
            variants.append(" ".join(combo))
    # de-dupe while preserving order
    seen = set()
    uniq = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def not_change_inputs():
    """
    Cases that MUST NOT change:
      - emails (localpart equals first/last)
      - URLs/handles (path equals first/last)
      - inside-word substring (e.g., First + 'on')
      - filesystem-ish path segment (/u/<first>/repos)
      - glued possessives without boundary after 's (First’sTeam / First'sTeam)
    """
    cases = {
        f"Contact: {FIRST_L}@company.com",
        f"Profile: https://x.com/{FIRST_L}",
        f"We use {FIRST}on devices",       # inside-word, no boundary
        f"Path: /u/{FIRST_L}/repos",
        f"{FIRST}’sTeam did this",         # glued possessive with curly apos
        f"{FIRST}'sTeam did this",         # glued possessive with straight apos
    }
    # If last token differs from first, add last-specific variants
    if LAST_L != FIRST_L:
        cases.update({
            f"Contact: {LAST_L}@company.com",
            f"Profile: https://x.com/{LAST_L}",
        })
    # Return in stable order
    return sorted(cases)


# Curly apostrophe cases (’)
@pytest.mark.parametrize("variant", all_variants())
def test_curly_apos_with_punct(variant):
    assert norm(f"{variant}’s.") == "your."
    assert norm(f"{variant}’s,") == "your,"
    assert norm(f"{variant}’s?") == "your?"
    assert norm(f"{variant}’s!") == "your!"
    assert norm(f"{variant}’s;") == "your;"
    assert norm(f"{variant}’s:") == "your:"
    assert norm(f"Tell me about {variant}’s.") == "Tell me about your."


# Straight apostrophe cases (')
@pytest.mark.parametrize("variant", all_variants())
def test_straight_apos_with_punct(variant):
    assert norm(f"{variant}'s.") == "your."
    assert norm(f"{variant}'s,") == "your,"
    assert norm(f"{variant}'s?") == "your?"
    assert norm(f"{variant}'s!") == "your!"
    assert norm(f"{variant}'s;") == "your;"
    assert norm(f"{variant}'s:") == "your:"
    assert norm(f"Tell me about {variant}'s.") == "Tell me about your."


# Bare name used as subject/object becomes "I"
@pytest.mark.parametrize("variant", all_variants())
def test_bare_name_subject_object(variant):
    assert norm(f"Tell me about {variant}.") == "Tell me about I."
    assert norm(f"What did {variant} do?") == "What did I do?"


# Should NOT change (dynamic)
@pytest.mark.parametrize("inp", not_change_inputs())
def test_should_not_change(inp):
    assert norm(inp) == inp
