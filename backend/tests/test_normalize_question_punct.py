import pytest
from api.retrieval import normalize_question_for_first_person as norm

# Curly apostrophe cases (’)
@pytest.mark.parametrize("inp,out", [
    ("Omer’s.", "your."),
    ("Omer’s,", "your,"),
    ("Omer’s?", "your?"),
    ("Omer’s!", "your!"),
    ("Omer’s;", "your;"),
    ("Omer’s:", "your:"),
    ("Tell me about Omer’s.", "Tell me about your."),
])
def test_curly_apos_with_punct(inp, out):
    assert norm(inp) == out

# Straight apostrophe cases (')
@pytest.mark.parametrize("inp,out", [
    ("Omer's.", "your."),
    ("Omer's,", "your,"),
    ("Omer's?", "your?"),
    ("Omer's!", "your!"),
    ("Omer's;", "your;"),
    ("Omer's:", "your:"),
    ("Tell me about Omer's.", "Tell me about your."),
])
def test_straight_apos_with_punct(inp, out):
    assert norm(inp) == out

# Full name possessive, mixed punctuation
@pytest.mark.parametrize("inp,out", [
    ("Omer Reznik’s.", "your."),
    ("Omer Reznik's,", "your,"),
])
def test_fullname_possessive_with_punct(inp, out):
    assert norm(inp) == out

# Should NOT change (emails, URLs, inside longer tokens, path segments)
@pytest.mark.parametrize("inp", [
    "Contact: omer@company.com",
    "Profile: https://x.com/omer",
    "We use Omeron devices",
    "Path: /u/omer/repos",
    "Omer’sTeam did this",
    "Omer'sTeam did this",
])
def test_should_not_change(inp):
    assert norm(inp) == inp
