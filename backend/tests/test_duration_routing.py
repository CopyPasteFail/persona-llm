"""Unit tests for deterministic duration intent and family resolution."""

from __future__ import annotations

from api import deterministic_duration


def test_is_duration_intent_detects_years_of_experience_questions() -> None:
    """Duration intent should be detected for explicit years-of-experience prompts."""
    assert deterministic_duration.is_duration_intent(
        "How many years of experience do you have in DevOps?"
    )


def test_is_duration_intent_returns_false_for_non_duration_questions() -> None:
    """Duration intent should not trigger for general project questions."""
    assert not deterministic_duration.is_duration_intent(
        "What did you build with Kubernetes at Acme?"
    )


def test_resolve_families_for_question_maps_infra_terms() -> None:
    """Infra-oriented keywords should map to infra_ops family."""
    mapped_families = deterministic_duration.resolve_families_for_question(
        "How many years of DevOps and SRE experience do you have?"
    )
    assert mapped_families == ["infra_ops"]


def test_resolve_families_for_question_supports_combined_domains() -> None:
    """Combined prompts should resolve multiple duration families."""
    mapped_families = deterministic_duration.resolve_families_for_question(
        "How many years across devops and marketing did you work?"
    )
    assert mapped_families == ["infra_ops", "marketing"]


def test_compute_duration_for_question_returns_union_and_breakdown_for_combined_query() -> None:
    """Combined-domain queries should return union totals and per-family breakdown."""
    chunks_by_id = {
        "chunk-devops": {
            "id": "chunk-devops",
            "text": "stub",
            "metadata": {
                "doc_id": "doc-1",
                "chunk_id": "chunk-devops",
                "section": "Experience",
                "start_year": 2020,
                "end_year": 2022,
                "extras": {
                    "employer": "Acme",
                    "title": "Platform Engineer",
                    "stint_domains": ["devops", "marketing"],
                },
            },
        },
        "chunk-marketing": {
            "id": "chunk-marketing",
            "text": "stub",
            "metadata": {
                "doc_id": "doc-1",
                "chunk_id": "chunk-marketing",
                "section": "Experience",
                "start_year": 2023,
                "end_year": 2024,
                "extras": {
                    "employer": "Beta",
                    "title": "Marketing Manager",
                    "stint_domains": ["marketing"],
                },
            },
        },
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years across devops and marketing?",
        current_year=2026,
    )
    answer = deterministic_duration.format_duration_answer(result)

    assert result.resolved_family_keys == ("infra_ops", "marketing")
    assert result.union_total_years == 5
    assert "Breakdown by family (years): infra_ops: 3; marketing: 5." in answer
    assert "Breakdown totals can overlap" in answer
