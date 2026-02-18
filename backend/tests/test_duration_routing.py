"""Unit tests for deterministic duration intent and family resolution."""

from __future__ import annotations

from api import deterministic_duration


def test_is_duration_intent_detects_years_of_experience_questions() -> None:
    """Duration intent should be detected for explicit years-of-experience prompts."""
    assert deterministic_duration.is_duration_intent(
        "How many years of experience do you have in DevOps?"
    )


def test_is_duration_intent_detects_how_much_experience_questions() -> None:
    """Duration intent should be detected for 'how much experience' phrasing."""
    assert deterministic_duration.is_duration_intent(
        "How much of experience do you have in CI/CD?"
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
            "chunk_id": "chunk-devops",
            "doc_id": "doc-1",
            "text": "stub",
            "section": "Experience",
            "start_year": 2020,
            "end_year": 2022,
            "extras": {
                "employer": "Acme",
                "title": "Platform Engineer",
                "stint_domains": ["devops", "marketing"],
            },
        },
        "chunk-marketing": {
            "chunk_id": "chunk-marketing",
            "doc_id": "doc-1",
            "text": "stub",
            "section": "Experience",
            "start_year": 2023,
            "end_year": 2024,
            "extras": {
                "employer": "Beta",
                "title": "Marketing Manager",
                "stint_domains": ["marketing"],
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


def test_compute_duration_for_question_filters_web_cms_by_specific_alias_evidence() -> None:
    """WordPress prompts should count only stints with explicit WordPress evidence."""
    chunks_by_id = {
        "chunk-wordpress": {
            "chunk_id": "chunk-wordpress",
            "doc_id": "doc-1",
            "text": "Built and maintained a WordPress website",
            "section": "Experience",
            "start_year": 2020,
            "end_year": 2023,
            "topics": ["wordpress", "content-automation"],
            "extras": {
                "employer": "The Free Genie",
                "title": "Website Owner",
                "stint_domains": ["software_engineering", "frontend", "product"],
                "tech": ["WordPress"],
            },
        },
        "chunk-non-wordpress": {
            "chunk_id": "chunk-non-wordpress",
            "doc_id": "doc-2",
            "text": "Led product strategy and roadmap execution",
            "section": "Experience",
            "start_year": 2017,
            "end_year": 2019,
            "topics": ["roadmap", "product"],
            "extras": {
                "employer": "Example Startup",
                "title": "Product Owner",
                "stint_domains": ["software_engineering", "frontend", "product"],
            },
        },
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of experience do you have with WordPress?",
        current_year=2026,
    )

    assert result.resolved_family_keys == ("web_cms",)
    assert result.union_total_years == 4
    assert len(result.union_matched_stints) == 1
    assert result.union_matched_stints[0].employer == "The Free Genie"


def test_compute_duration_for_question_unmapped_skill_requires_direct_evidence() -> None:
    """Unmapped skill prompts should not fall back to all-domain union years."""
    chunks_by_id = {
        "chunk-devops": {
            "chunk_id": "chunk-devops",
            "doc_id": "doc-1",
            "text": "Built CI/CD pipelines in Kubernetes environments",
            "section": "Experience",
            "start_year": 2020,
            "end_year": 2023,
            "topics": ["cicd", "kubernetes"],
            "extras": {
                "employer": "Acme",
                "title": "DevOps Engineer",
                "stint_domains": ["devops", "platform"],
                "tech": ["Jenkins", "Terraform"],
            },
        },
        "chunk-cpp-skills-only": {
            "chunk_id": "chunk-cpp-skills-only",
            "doc_id": "doc-2",
            "text": "General software engineering leadership",
            "section": "Experience",
            "start_year": 2011,
            "end_year": 2019,
            "topics": ["software", "backend"],
            "extras": {
                "employer": "Beta",
                "title": "Software Engineer",
                "stint_domains": ["software_engineering", "backend"],
                "tech": ["Python", "Go"],
            },
        },
    }

    cpp_result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of experience do you have with C++?",
        current_year=2026,
    )
    assert cpp_result.union_total_years == 0
    assert len(cpp_result.union_matched_stints) == 0
    assert len(cpp_result.family_breakdown) == 0

    cicd_result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of experience do you have in CI/CD?",
        current_year=2026,
    )
    assert cicd_result.union_total_years == 4
    assert len(cicd_result.union_matched_stints) == 1
