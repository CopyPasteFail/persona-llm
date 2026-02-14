"""Unit tests for deterministic duration interval aggregation."""

from __future__ import annotations

from api import deterministic_duration


def _chunk(
    *,
    chunk_id: str,
    start_year: int,
    end_year: int | None,
    stint_domains: list[str] | None = None,
    employer: str = "Acme",
    title: str = "Engineer",
    section: str = "Experience",
) -> dict[str, object]:
    """Build a minimal chunk payload for duration computation tests."""
    return {
        "id": chunk_id,
        "text": "stub",
        "metadata": {
            "doc_id": "doc-1",
            "chunk_id": chunk_id,
            "section": section,
            "start_year": start_year,
            "end_year": end_year,
            "extras": {
                "stint_domains": stint_domains or ["devops"],
                "employer": employer,
                "title": title,
            },
        },
    }


def test_compute_duration_for_question_merges_overlapping_intervals() -> None:
    """Overlapping intervals should merge into one continuous range."""
    chunks_by_id = {
        "chunk-1": _chunk(
            chunk_id="chunk-1",
            start_year=2018,
            end_year=2020,
            employer="Acme",
            title="SRE",
        ),
        "chunk-2": _chunk(
            chunk_id="chunk-2",
            start_year=2020,
            end_year=2022,
            employer="Beta",
            title="SRE",
        ),
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of devops experience do you have?",
        current_year=2026,
    )

    assert result.union_total_years == 5
    assert len(result.union_matched_stints) == 2
    assert result.union_merged_intervals == (
        deterministic_duration.DurationInterval(start_year=2018, end_year=2022),
    )


def test_compute_duration_for_question_merges_adjacent_intervals() -> None:
    """Adjacent year ranges should merge into one range for continuous reporting."""
    chunks_by_id = {
        "chunk-1": _chunk(
            chunk_id="chunk-1",
            start_year=2018,
            end_year=2020,
            employer="Acme",
            title="DevOps",
        ),
        "chunk-2": _chunk(
            chunk_id="chunk-2",
            start_year=2021,
            end_year=2021,
            employer="Beta",
            title="DevOps",
        ),
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of devops experience do you have?",
        current_year=2026,
    )

    assert result.union_total_years == 4
    assert result.union_merged_intervals == (
        deterministic_duration.DurationInterval(start_year=2018, end_year=2021),
    )


def test_compute_duration_for_question_keeps_disjoint_intervals_separate() -> None:
    """Disjoint year ranges should remain separate and sum independently."""
    chunks_by_id = {
        "chunk-1": _chunk(
            chunk_id="chunk-1",
            start_year=2018,
            end_year=2019,
            employer="Acme",
            title="Platform Engineer",
        ),
        "chunk-2": _chunk(
            chunk_id="chunk-2",
            start_year=2022,
            end_year=2023,
            employer="Beta",
            title="Platform Engineer",
        ),
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of platform experience do you have?",
        current_year=2026,
    )

    assert result.union_total_years == 4
    assert result.union_merged_intervals == (
        deterministic_duration.DurationInterval(start_year=2018, end_year=2019),
        deterministic_duration.DurationInterval(start_year=2022, end_year=2023),
    )


def test_compute_duration_for_question_uses_current_year_for_open_ended_stints() -> None:
    """Missing end-year should resolve to the injected current year."""
    chunks_by_id = {
        "chunk-1": _chunk(
            chunk_id="chunk-1",
            start_year=2024,
            end_year=None,
            employer="Acme",
            title="SRE",
        )
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of devops experience do you have?",
        current_year=2026,
    )

    assert result.union_total_years == 3
    assert result.union_merged_intervals == (
        deterministic_duration.DurationInterval(start_year=2024, end_year=2026),
    )


def test_compute_duration_for_question_excludes_non_experience_chunks() -> None:
    """Only section == Experience should contribute to duration totals."""
    chunks_by_id = {
        "summary-1": _chunk(
            chunk_id="summary-1",
            start_year=2018,
            end_year=2020,
            section="Summary",
        ),
        "experience-1": _chunk(
            chunk_id="experience-1",
            start_year=2021,
            end_year=2022,
            section="Experience",
        ),
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of devops experience do you have?",
        current_year=2026,
    )

    assert result.union_total_years == 2
    assert result.union_merged_intervals == (
        deterministic_duration.DurationInterval(start_year=2021, end_year=2022),
    )


def test_format_based_on_stints_uses_distinct_contributing_stints_only() -> None:
    """Based-on output should dedupe duplicate chunks and exclude non-matching stints."""
    chunks_by_id = {
        "devops-1": _chunk(
            chunk_id="devops-1",
            start_year=2019,
            end_year=2020,
            stint_domains=["devops"],
            employer="Acme",
            title="SRE",
        ),
        "devops-2": _chunk(
            chunk_id="devops-2",
            start_year=2019,
            end_year=2020,
            stint_domains=["sre"],
            employer="Acme",
            title="SRE",
        ),
        "marketing-1": _chunk(
            chunk_id="marketing-1",
            start_year=2021,
            end_year=2022,
            stint_domains=["marketing"],
            employer="Beta",
            title="Marketing Manager",
        ),
    }

    result = deterministic_duration.compute_duration_for_question(
        chunks_by_id,
        question="How many years of DevOps experience do you have?",
        current_year=2026,
    )
    based_on_text = deterministic_duration.format_based_on_stints(result.union_matched_stints)

    assert len(result.union_matched_stints) == 1
    assert based_on_text.count("Acme, SRE, 2019-2020") == 1
    assert "Beta" not in based_on_text
