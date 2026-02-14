"""Tests for deterministic answer bullet deduplication in chat orchestration."""

from api import rag_chat_orchestrator


def test_deduplicate_answer_bullets_drops_single_related_experience_duplicate() -> None:
    """Duplicate bullet matching single related experience should be removed.

    What is tested:
        Redundant bullet removal when the first sentence already states one related item.
    How it's tested:
        Call the dedupe helper with one repeated bullet and one distinct bullet.
    Expected result format:
        First sentence remains, duplicate bullet is removed, distinct bullet remains.
    """
    raw_answer = (
        "I have no direct experience in education, but I do have related experience: "
        "I run customer training.\n"
        "- I run customer training.\n"
        "- I trained enterprise customers on onboarding workflows."
    )

    deduped_answer = rag_chat_orchestrator._deduplicate_answer_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert "- I run customer training." not in deduped_answer
    assert (
        "- I trained enterprise customers on onboarding workflows."
        in deduped_answer
    )


def test_deduplicate_answer_bullets_handles_multiple_related_experience_items() -> None:
    """Duplicate bullets matching any related-experience list item should be removed.

    What is tested:
        Fragment-level dedupe against comma/connector-separated related items.
    How it's tested:
        Use a first sentence with multiple related items and repeated bullets.
    Expected result format:
        Bullets duplicating listed related items are removed, unique bullets remain.
    """
    raw_answer = (
        "I have no direct experience in education, but I do have related experience: "
        "customer training, onboarding, and enablement.\n"
        "- Customer training.\n"
        "- onboarding\n"
        "- I built role-based enablement plans."
    )

    deduped_answer = rag_chat_orchestrator._deduplicate_answer_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert "- Customer training." not in deduped_answer
    assert "- onboarding" not in deduped_answer
    assert "- I built role-based enablement plans." in deduped_answer


def test_deduplicate_answer_bullets_keeps_non_overlapping_bullets() -> None:
    """Non-overlapping bullets should remain unchanged.

    What is tested:
        No false positive removals when bullets add new facts.
    How it's tested:
        Use a first sentence plus distinct bullets with different details.
    Expected result format:
        Output matches input content.
    """
    raw_answer = (
        "I have no direct experience in education, but I do have related experience: "
        "customer training.\n"
        "- I trained 40 customers quarterly.\n"
        "- I built onboarding playbooks."
    )

    deduped_answer = rag_chat_orchestrator._deduplicate_answer_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert deduped_answer == raw_answer


def test_insert_transition_line_before_bullets_adds_transition_line() -> None:
    """Bullet answers should include a transition line before the first bullet.

    What is tested:
        Transition smoothing when answer includes bullet lines.
    How it's tested:
        Pass an answer with a one-line summary and bullets.
    Expected result format:
        A short transition line appears before the first bullet.
    """
    raw_answer = (
        "I have extensive experience with Kubernetes.\n"
        "- I deployed and upgraded clusters.\n"
        "- I maintain Helm charts for production."
    )

    formatted_answer = rag_chat_orchestrator._insert_transition_line_before_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert (
        "I have extensive experience with Kubernetes.\nMore specifically:\n- "
        in formatted_answer
    )
    assert "- I deployed and upgraded clusters." in formatted_answer
    assert "- I maintain Helm charts for production." in formatted_answer


def test_insert_transition_line_before_bullets_keeps_single_line_answer() -> None:
    """Single-line answers should remain unchanged.

    What is tested:
        No punctuation rewrite when no bullets exist.
    How it's tested:
        Pass a one-line answer without any bullet lines.
    Expected result format:
        Output matches input text exactly.
    """
    raw_answer = "I have extensive experience with Kubernetes."

    formatted_answer = rag_chat_orchestrator._insert_transition_line_before_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert formatted_answer == raw_answer


def test_insert_transition_line_before_bullets_does_not_duplicate_transition_line() -> None:
    """Existing transition lines should not be inserted twice.

    What is tested:
        Idempotent transition insertion for already-formatted answers.
    How it's tested:
        Pass an answer that already contains the transition line before bullets.
    Expected result format:
        Output remains unchanged.
    """
    raw_answer = (
        "I have extensive experience with Kubernetes.\n"
        "More specifically:\n"
        "- I deployed and upgraded clusters."
    )

    formatted_answer = rag_chat_orchestrator._insert_transition_line_before_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert formatted_answer == raw_answer


def test_insert_transition_line_before_bullets_removes_model_bridge_lines() -> None:
    """Model-emitted bridge lines should be normalized to one canonical transition.

    What is tested:
        Transition normalization when the model includes its own bridge sentence.
    How it's tested:
        Pass an answer that includes a non-bullet bridge line before bullets.
    Expected result format:
        Only "More specifically:" remains between the first sentence and bullets.
    """
    raw_answer = (
        "I have no direct experience in teaching, but I do have related experience: "
        "I run customer training and I led a mentoring program.\n"
        "Here's more about my related experience:\n"
        "- I led a mentoring program at Cognyte.\n"
        "- I reinforce adoption through technical walkthroughs."
    )

    formatted_answer = rag_chat_orchestrator._insert_transition_line_before_bullets(  # pyright: ignore[reportPrivateUsage]
        raw_answer
    )

    assert "Here's more about my related experience:" not in formatted_answer
    assert (
        "I have no direct experience in teaching, but I do have related experience: "
        "I run customer training and I led a mentoring program.\n"
        "More specifically:\n"
        "- I led a mentoring program at Cognyte.\n"
        "- I reinforce adoption through technical walkthroughs."
        == formatted_answer
    )
