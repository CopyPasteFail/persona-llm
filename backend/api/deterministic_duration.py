"""Deterministic duration routing helpers for metadata-only experience answers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from . import duration_domain_config

SECTION_EXPERIENCE = "Experience"
EXTRAS_KEY = "extras"
EXTRAS_EMPLOYER_KEY = "employer"
EXTRAS_TITLE_KEY = "title"
EXTRAS_STINT_DOMAINS_KEY = "stint_domains"
EXTRAS_TECH_KEY = "tech"
_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9+#/.-]*")
_UNMAPPED_SKILL_STOPWORDS = frozenset(
    {
        "how",
        "much",
        "many",
        "years",
        "year",
        "experience",
        "do",
        "you",
        "have",
        "of",
        "the",
        "a",
        "an",
        "total",
        "overall",
        "all",
    }
)
_DURATION_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow many years\b", re.IGNORECASE),
    re.compile(r"\byears of experience\b", re.IGNORECASE),
    re.compile(r"\bhow much\b.*\bexperience\b", re.IGNORECASE),
    re.compile(r"\bhow long\b", re.IGNORECASE),
    re.compile(r"\btotal years\b", re.IGNORECASE),
)
ZERO_MATCH_EXPERIENCE_MESSAGE = (
    "I can't compute this from the Experience stints in the current dataset."
)


@dataclass(frozen=True)
class DurationInterval:
    """Closed-year interval used for deterministic duration calculations.

    Inputs:
    - start_year: Inclusive start year.
    - end_year: Inclusive end year.

    Output:
    - Immutable year interval consumed by merge and formatting helpers.
    """

    start_year: int
    end_year: int


@dataclass(frozen=True)
class DurationStint:
    """Deduplicated Experience stint used by deterministic duration routing.

    Inputs:
    - doc_id: Source document identifier.
    - employer: Employer name from chunk metadata.
    - title: Stint title from chunk metadata.
    - start_year: Inclusive start year.
    - end_year: Raw end year (None means present).
    - resolved_end_year: End year normalized using injected current year.
    - stint_domains: Canonical fine-grained domain labels.
    - evidence_terms: Normalized metadata terms used for strict alias filtering.

    Output:
    - Immutable stint payload consumed by union and family computations.
    """

    doc_id: str
    employer: str
    title: str
    start_year: int
    end_year: int | None
    resolved_end_year: int
    stint_domains: frozenset[str]
    evidence_terms: frozenset[str]


@dataclass(frozen=True)
class FamilyDurationBreakdown:
    """Per-family deterministic duration totals.

    Inputs:
    - family_key: Configured family key.
    - matched_stint_count: Number of unique stints matched for this family.
    - merged_intervals: Merged intervals for this family only.
    - total_years: Inclusive years across merged intervals.

    Output:
    - Immutable payload used in deterministic answer formatting.
    """

    family_key: str
    matched_stint_count: int
    merged_intervals: tuple[DurationInterval, ...]
    total_years: int


@dataclass(frozen=True)
class DurationComputationResult:
    """Deterministic duration result with union totals and family breakdown.

    Inputs:
    - current_year: Year injected by the orchestrator.
    - resolved_family_keys: Family keys matched from the question.
    - union_matched_stints: Unique stints included in the union total.
    - union_merged_intervals: Merged union intervals across selected labels.
    - union_total_years: Inclusive union total years.
    - family_breakdown: Per-family totals for requested or matched families.

    Output:
    - Immutable payload consumed by deterministic routing formatters.
    """

    current_year: int
    resolved_family_keys: tuple[str, ...]
    union_matched_stints: tuple[DurationStint, ...]
    union_merged_intervals: tuple[DurationInterval, ...]
    union_total_years: int
    family_breakdown: tuple[FamilyDurationBreakdown, ...]


@dataclass(frozen=True)
class _ResolvedInterval:
    """Internal interval payload preserving raw and normalized end-year values."""

    start_year: int
    end_year: int | None
    resolved_end_year: int


@dataclass
class _MutableStint:
    """Internal mutable stint container used during chunk aggregation."""

    doc_id: str
    employer: str
    title: str
    start_year: int
    end_year: int | None
    resolved_end_year: int
    stint_domains: set[str]
    evidence_terms: set[str]


def is_duration_intent(question: str) -> bool:
    """Return True when the question asks for duration in years.

    Inputs:
    - question: User question text.

    Output:
    - Boolean classification for duration intent.

    Edge cases:
    - Empty or whitespace-only text returns False.
    """

    normalized_question = (question or "").strip()
    if not normalized_question:
        return False
    return any(pattern.search(normalized_question) for pattern in _DURATION_INTENT_PATTERNS)


def resolve_families_for_question(question: str) -> list[str]:
    """Resolve duration family keys that match the given question text.

    Inputs:
    - question: User question text.

    Output:
    - Ordered list of matching family keys.

    Edge cases:
    - Returns an empty list when no configured family alias matches.
    """

    return duration_domain_config.resolve_families_for_query(question)


def compute_duration_for_question(
    chunks_by_id: Mapping[str, Mapping[str, Any]],
    *,
    question: str,
    current_year: int,
) -> DurationComputationResult:
    """Compute deterministic duration totals from Experience metadata.

    Inputs:
    - chunks_by_id: Chunk-store snapshot keyed by chunk id.
    - question: User question used to resolve target families.
    - current_year: UTC year injected by the orchestrator.

    Output:
    - DurationComputationResult with union totals and per-family breakdown.

    Edge cases:
    - Filters strictly to ``section == "Experience"`` chunks.
    - Unknown families resolve to empty family selection and trigger all-domain union.
    - Missing/invalid metadata is ignored.
    """

    config = duration_domain_config.get_config()
    stints = _extract_experience_stints(
        chunks_by_id,
        current_year=int(current_year),
        canonical_labels=config.canonical_labels,
    )
    resolved_family_keys = tuple(resolve_families_for_question(question))
    family_specific_aliases = _resolve_non_generic_aliases_by_family(
        question=question,
        resolved_family_keys=resolved_family_keys,
    )
    unmapped_required_terms = _resolve_required_skill_terms_for_unmapped_query(
        question=question,
        resolved_family_keys=resolved_family_keys,
    )

    if resolved_family_keys:
        union_labels: set[str] = set()
        for family_key in resolved_family_keys:
            union_labels.update(duration_domain_config.accepted_labels_for_family(family_key))
        breakdown_family_keys = list(resolved_family_keys)
    else:
        union_labels = {
            label
            for stint in stints
            for label in stint.stint_domains
            if label in config.canonical_labels
        }
        breakdown_family_keys: list[str] = []
        if not unmapped_required_terms:
            breakdown_family_keys = [
                family_key
                for family_key in config.families
                if _family_has_matching_stints(stints, family_key=family_key)
            ]

    union_matched_stints = tuple(
        stint
        for stint in stints
        if _stint_matches_selected_families(
            stint,
            resolved_family_keys=resolved_family_keys,
            union_labels=union_labels,
            family_specific_aliases=family_specific_aliases,
            unmapped_required_terms=unmapped_required_terms,
        )
    )
    union_intervals = _merge_intervals(
        [
            DurationInterval(stint.start_year, stint.resolved_end_year)
            for stint in union_matched_stints
        ]
    )

    family_breakdown: list[FamilyDurationBreakdown] = []
    for family_key in breakdown_family_keys:
        accepted_labels = duration_domain_config.accepted_labels_for_family(family_key)
        required_aliases = family_specific_aliases.get(family_key, ())
        matched_stints = [
            stint
            for stint in stints
            if stint.stint_domains & accepted_labels
            and _stint_matches_any_alias(stint, required_aliases)
            and _stint_matches_unmapped_terms(stint, unmapped_required_terms)
        ]
        merged_intervals = _merge_intervals(
            [
                DurationInterval(stint.start_year, stint.resolved_end_year)
                for stint in matched_stints
            ]
        )
        family_breakdown.append(
            FamilyDurationBreakdown(
                family_key=family_key,
                matched_stint_count=len(matched_stints),
                merged_intervals=tuple(merged_intervals),
                total_years=_sum_interval_years(merged_intervals),
            )
        )

    return DurationComputationResult(
        current_year=int(current_year),
        resolved_family_keys=resolved_family_keys,
        union_matched_stints=union_matched_stints,
        union_merged_intervals=tuple(union_intervals),
        union_total_years=_sum_interval_years(union_intervals),
        family_breakdown=tuple(family_breakdown),
    )


def format_duration_answer(result: DurationComputationResult) -> str:
    """Build a deterministic answer string from computed duration data.

    Inputs:
    - result: Duration computation payload.

    Output:
    - Stable answer string with union total and family breakdown.

    Edge cases:
    - Zero matched stints return a deterministic no-match answer.
    - Zero totals return a deterministic insufficient-metadata answer.
    """

    if not result.union_matched_stints:
        return ZERO_MATCH_EXPERIENCE_MESSAGE

    if result.union_total_years <= 0:
        return (
            "TLDR: I do not have enough dated Experience metadata to compute years.\n"
            "- Ask about a specific stint or domain and I will answer from indexed context."
        )

    summary_target = ""
    if result.resolved_family_keys:
        summary_target = f" across {', '.join(result.resolved_family_keys)}"

    union_ranges_text = ", ".join(
        _format_year_range(interval.start_year, interval.end_year)
        for interval in result.union_merged_intervals
    )
    family_parts = [
        f"{family.family_key}: {family.total_years}"
        for family in result.family_breakdown
    ]
    breakdown_line = "none"
    if family_parts:
        breakdown_line = "; ".join(family_parts)

    answer_lines = [
        f"TLDR: I have about {result.union_total_years} years of experience{summary_target}.",
        f"- Union total (deduplicated): {result.union_total_years} years.",
        f"- Covered years: {union_ranges_text}.",
        f"- Breakdown by family (years): {breakdown_line}.",
    ]
    if len(result.family_breakdown) > 1:
        answer_lines.append(
            "- Breakdown totals can overlap when one stint maps to multiple families."
        )
    return "\n".join(answer_lines)


def format_based_on_stints(
    stints: tuple[DurationStint, ...],
    *,
    max_items: int = 8,
) -> str:
    """Format a compact deterministic "Based on" list from matched stints.

    Inputs:
    - stints: Matched stints included in the union total.
    - max_items: Maximum stints to render in the output list.

    Output:
    - One-line summary listing employer, title, and date range per stint.

    Edge cases:
    - Empty stints return a deterministic "none" marker.
    """

    if not stints:
        return "Based on: none."

    formatted_stints: list[str] = []
    for stint in stints[:max_items]:
        end_label = "present" if stint.end_year is None else str(stint.end_year)
        formatted_stints.append(
            f"{stint.employer}, {stint.title}, {stint.start_year}-{end_label}"
        )

    if len(stints) > max_items:
        remaining_count = len(stints) - max_items
        formatted_stints.append(f"+{remaining_count} more")

    return "Based on: " + "; ".join(formatted_stints) + "."


def _extract_experience_stints(
    chunks_by_id: Mapping[str, Mapping[str, Any]],
    *,
    current_year: int,
    canonical_labels: frozenset[str],
) -> list[DurationStint]:
    """Extract and deduplicate Experience stints from flat chunk records.

    Inputs:
    - chunks_by_id: Chunk-store snapshot keyed by chunk id.
    - current_year: Year used to resolve open-ended stints.
    - canonical_labels: Allowed fine-grained labels from config.

    Output:
    - Sorted list of deduplicated stints.

    Edge cases:
    - Ignores chunks that are not ``section == "Experience"``.
    - Ignores chunks with missing employer/title/years/stint_domains fields.
    - Deduplicates by ``(doc_id, employer, title, start_year, end_year)``.
    """

    deduplicated_stints: dict[tuple[str, str, str, int, int | None], _MutableStint] = {}

    for chunk in chunks_by_id.values():
        if chunk.get("section") != SECTION_EXPERIENCE:
            continue

        interval = _resolve_interval(chunk, current_year=current_year)
        if interval is None:
            continue

        extras_object = chunk.get(EXTRAS_KEY)
        if not isinstance(extras_object, Mapping):
            continue
        extras = extras_object

        employer = _required_string(extras.get(EXTRAS_EMPLOYER_KEY))
        title = _required_string(extras.get(EXTRAS_TITLE_KEY))
        if not employer or not title:
            continue

        stint_domains = _resolve_stint_domains(
            extras.get(EXTRAS_STINT_DOMAINS_KEY),
            canonical_labels=canonical_labels,
        )
        if not stint_domains:
            continue
        evidence_terms = _extract_evidence_terms(chunk=chunk, extras=extras)

        normalized_employer = employer.lower()
        normalized_title = title.lower()
        doc_id = _normalized_optional_string(chunk.get("doc_id"))
        dedupe_key = (
            doc_id,
            normalized_employer,
            normalized_title,
            interval.start_year,
            interval.end_year,
        )
        existing_stint = deduplicated_stints.get(dedupe_key)
        if existing_stint is None:
            deduplicated_stints[dedupe_key] = _MutableStint(
                doc_id=doc_id,
                employer=employer,
                title=title,
                start_year=interval.start_year,
                end_year=interval.end_year,
                resolved_end_year=interval.resolved_end_year,
                stint_domains=set(stint_domains),
                evidence_terms=set(evidence_terms),
            )
            continue

        existing_stint.stint_domains.update(stint_domains)
        existing_stint.evidence_terms.update(evidence_terms)

    sorted_stints = sorted(
        deduplicated_stints.values(),
        key=lambda stint: (
            stint.start_year,
            stint.resolved_end_year,
            stint.employer.lower(),
            stint.title.lower(),
            stint.doc_id,
        ),
    )
    return [
        DurationStint(
            doc_id=stint.doc_id,
            employer=stint.employer,
            title=stint.title,
            start_year=stint.start_year,
            end_year=stint.end_year,
            resolved_end_year=stint.resolved_end_year,
            stint_domains=frozenset(stint.stint_domains),
            evidence_terms=frozenset(stint.evidence_terms),
        )
        for stint in sorted_stints
    ]


def _extract_evidence_terms(
    *,
    chunk: Mapping[str, Any],
    extras: Mapping[str, Any],
) -> set[str]:
    """Extract normalized terms used for alias-level stint filtering.

    Inputs:
    - chunk: Source chunk used in stint aggregation.
    - extras: Chunk extras mapping.

    Output:
    - Normalized token set derived from text and metadata.

    Edge cases:
    - Missing fields produce an empty set.
    - Non-string list entries are ignored.
    """

    evidence_terms: set[str] = set()
    for token in _tokenize_text(chunk.get("text")):
        evidence_terms.add(token)

    topics_value = chunk.get("topics")
    if isinstance(topics_value, (list, tuple, set, frozenset)):
        for topic in topics_value:
            if not isinstance(topic, str):
                continue
            for token in _tokenize_text(topic):
                evidence_terms.add(token)

    tags_value = chunk.get("tags")
    if isinstance(tags_value, (list, tuple, set, frozenset)):
        for tag in tags_value:
            if not isinstance(tag, str):
                continue
            normalized_tag = tag.strip().lower()
            if ":" in normalized_tag:
                normalized_tag = normalized_tag.split(":", maxsplit=1)[1]
            for token in _tokenize_text(normalized_tag):
                evidence_terms.add(token)

    tech_value = extras.get(EXTRAS_TECH_KEY)
    if isinstance(tech_value, (list, tuple, set, frozenset)):
        for tech_item in tech_value:
            if not isinstance(tech_item, str):
                continue
            for token in _tokenize_text(tech_item):
                evidence_terms.add(token)

    return evidence_terms


def _resolve_non_generic_aliases_by_family(
    *,
    question: str,
    resolved_family_keys: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """Resolve question aliases that require stricter per-stint evidence.

    Inputs:
    - question: User question text.
    - resolved_family_keys: Families already matched for this question.

    Output:
    - Mapping of family key to matched non-generic aliases.

    Edge cases:
    - Generic family/member aliases do not activate strict evidence filtering.
    - Families with no non-generic matched aliases are omitted.
    """

    normalized_phrase_text, token_set = _normalize_question_tokens(question)
    config = duration_domain_config.get_config()
    family_aliases: dict[str, tuple[str, ...]] = {}

    for family_key in resolved_family_keys:
        family_config = config.families[family_key]
        matched_aliases: list[str] = []
        for alias in family_config.query_aliases:
            if _is_generic_family_alias(
                alias=alias,
                family_key=family_key,
                family_members=family_config.members,
            ):
                continue
            if _alias_matches_tokens(
                alias=alias,
                normalized_phrase_text=normalized_phrase_text,
                token_set=token_set,
            ):
                matched_aliases.append(alias)
        if matched_aliases:
            family_aliases[family_key] = tuple(matched_aliases)

    return family_aliases


def _normalize_question_tokens(question: str) -> tuple[str, set[str]]:
    """Normalize question text into phrase and token-set forms."""

    question_tokens = _TOKEN_PATTERN.findall((question or "").strip().lower())
    normalized_phrase_text = " ".join(question_tokens)
    return normalized_phrase_text, set(question_tokens)


def _is_generic_family_alias(
    *,
    alias: str,
    family_key: str,
    family_members: frozenset[str],
) -> bool:
    """Return True when an alias is a family/member synonym and not skill-specific."""

    normalized_alias = alias.strip().lower()
    if normalized_alias == family_key:
        return True
    if normalized_alias == family_key.replace("_", " "):
        return True
    if normalized_alias in family_members:
        return True
    return normalized_alias in {member.replace("_", " ") for member in family_members}


def _alias_matches_tokens(
    *,
    alias: str,
    normalized_phrase_text: str,
    token_set: set[str],
) -> bool:
    """Return whether an alias matches tokenized text using config matching rules."""

    alias_tokens = _TOKEN_PATTERN.findall(alias)
    if not alias_tokens:
        return False
    if len(alias_tokens) == 1:
        return alias_tokens[0] in token_set
    alias_phrase = " ".join(alias_tokens)
    return bool(alias_phrase) and alias_phrase in normalized_phrase_text


def _stint_matches_selected_families(
    stint: DurationStint,
    *,
    resolved_family_keys: tuple[str, ...],
    union_labels: set[str],
    family_specific_aliases: dict[str, tuple[str, ...]],
    unmapped_required_terms: frozenset[str],
) -> bool:
    """Return whether a stint should contribute to union totals for this question.

    Inputs:
    - stint: Candidate stint.
    - resolved_family_keys: Families resolved from the question.
    - union_labels: Union of accepted labels across selected families.
    - family_specific_aliases: Non-generic aliases requiring evidence matches.

    Output:
    - True when the stint should be counted in the union total.

    Edge cases:
    - Generic duration prompts with no resolved families use union labels only.
    - Family-specific aliases require explicit per-stint metadata evidence.
    """

    if not (stint.stint_domains & union_labels):
        return False
    if not resolved_family_keys:
        return _stint_matches_unmapped_terms(stint, unmapped_required_terms)

    for family_key in resolved_family_keys:
        family_labels = duration_domain_config.accepted_labels_for_family(family_key)
        if not (stint.stint_domains & family_labels):
            continue
        required_aliases = family_specific_aliases.get(family_key, ())
        if _stint_matches_any_alias(stint, required_aliases):
            return True
    return False


def _stint_matches_any_alias(stint: DurationStint, aliases: tuple[str, ...]) -> bool:
    """Return whether stint evidence matches any required alias.

    Inputs:
    - stint: Candidate stint with metadata-derived evidence terms.
    - aliases: Alias list that must be matched; empty means no strict filtering.

    Output:
    - True when no aliases are required, or at least one alias matches.
    """

    if not aliases:
        return True

    for alias in aliases:
        alias_tokens = _TOKEN_PATTERN.findall(alias)
        if not alias_tokens:
            continue
        if all(token in stint.evidence_terms for token in alias_tokens):
            return True
    return False


def _tokenize_text(value: object) -> list[str]:
    """Tokenize a free-form value into normalized alphanumeric tokens."""

    if not isinstance(value, str):
        return []
    return _TOKEN_PATTERN.findall(value.strip().lower())


def _resolve_required_skill_terms_for_unmapped_query(
    *,
    question: str,
    resolved_family_keys: tuple[str, ...],
) -> frozenset[str]:
    """Resolve explicit skill tokens for unmapped duration prompts.

    Inputs:
    - question: User question text.
    - resolved_family_keys: Families matched by config aliases.

    Output:
    - Required normalized terms when no family was matched; empty otherwise.

    Edge cases:
    - Generic prompts (for example "how many years of experience") return empty.
    - Keeps symbolic tokens such as ``ci/cd`` and ``c++``.
    """

    if resolved_family_keys:
        return frozenset()

    normalized_question = (question or "").strip().lower()
    for marker in (" with ", " in ", " on ", " for ", " about "):
        marker_index = normalized_question.find(marker)
        if marker_index < 0:
            continue
        tail_text = normalized_question[marker_index + len(marker):]
        tail_tokens = _TOKEN_PATTERN.findall(tail_text)
        required_terms = {
            token
            for token in tail_tokens
            if token not in _UNMAPPED_SKILL_STOPWORDS and len(token) > 1
        }
        return frozenset(required_terms)

    return frozenset()


def _stint_matches_unmapped_terms(
    stint: DurationStint,
    required_terms: frozenset[str],
) -> bool:
    """Return whether a stint matches strict required terms for unmapped queries."""

    if not required_terms:
        return True
    return all(term in stint.evidence_terms for term in required_terms)


def _family_has_matching_stints(
    stints: list[DurationStint],
    *,
    family_key: str,
) -> bool:
    """Return whether any stint intersects the family's accepted labels.

    Inputs:
    - stints: Deduplicated stints extracted from chunk metadata.
    - family_key: Configured family key.

    Output:
    - True when at least one stint matches the family, otherwise False.
    """

    accepted_labels = duration_domain_config.accepted_labels_for_family(family_key)
    return any(stint.stint_domains & accepted_labels for stint in stints)


def _resolve_stint_domains(
    raw_value: object,
    *,
    canonical_labels: frozenset[str],
) -> set[str]:
    """Resolve valid canonical stint labels from metadata extras.

    Inputs:
    - raw_value: ``extras.stint_domains`` metadata value.
    - canonical_labels: Allowed labels from the duration config.

    Output:
    - Set of validated canonical labels.

    Edge cases:
    - Invalid field shapes or unknown labels produce an empty set.
    - Supports list/tuple/set/frozenset values (immutable retrieval snapshots
      freeze JSON arrays to tuples).
    """

    if not isinstance(raw_value, (list, tuple, set, frozenset)):
        return set()

    labels: set[str] = set()
    for label_value in raw_value:
        if not isinstance(label_value, str):
            continue
        normalized_label = label_value.strip().lower()
        if not normalized_label:
            continue
        if normalized_label not in canonical_labels:
            continue
        labels.add(normalized_label)
    return labels


def _resolve_interval(
    chunk: Mapping[str, Any],
    *,
    current_year: int,
) -> _ResolvedInterval | None:
    """Resolve a valid closed interval from flat chunk year fields.

    Inputs:
    - chunk: Flat chunk mapping.
    - current_year: Year used when ``end_year`` is missing.

    Output:
    - _ResolvedInterval when valid; otherwise None.

    Edge cases:
    - Future-only or inverted intervals are rejected.
    - End year is clamped to ``current_year`` to avoid future-count drift.
    """

    start_year_value = chunk.get("start_year")
    if not isinstance(start_year_value, int):
        return None

    end_year_value = chunk.get("end_year")
    resolved_end_year = current_year
    if isinstance(end_year_value, int):
        resolved_end_year = end_year_value

    if start_year_value > current_year:
        return None
    if resolved_end_year > current_year:
        resolved_end_year = current_year
    if resolved_end_year < start_year_value:
        return None

    return _ResolvedInterval(
        start_year=start_year_value,
        end_year=end_year_value if isinstance(end_year_value, int) else None,
        resolved_end_year=resolved_end_year,
    )


def _required_string(value: object) -> str:
    """Return a trimmed non-empty string, otherwise empty."""

    if not isinstance(value, str):
        return ""
    normalized_value = value.strip()
    if not normalized_value:
        return ""
    return normalized_value


def _normalized_optional_string(value: object) -> str:
    """Normalize optional metadata strings for deterministic dedupe keys."""

    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _merge_intervals(intervals: list[DurationInterval]) -> list[DurationInterval]:
    """Merge sorted/unsorted intervals with overlap or direct adjacency.

    Inputs:
    - intervals: Raw intervals that may overlap or abut by one year.

    Output:
    - Merged intervals sorted by start year.

    Edge cases:
    - Empty input returns an empty list.
    - Adjacent years are merged for continuous-year reporting.
    """

    if not intervals:
        return []

    sorted_intervals = sorted(intervals, key=lambda interval: (interval.start_year, interval.end_year))
    merged_intervals: list[DurationInterval] = [sorted_intervals[0]]
    for next_interval in sorted_intervals[1:]:
        current_interval = merged_intervals[-1]
        if next_interval.start_year <= current_interval.end_year + 1:
            merged_intervals[-1] = DurationInterval(
                start_year=current_interval.start_year,
                end_year=max(current_interval.end_year, next_interval.end_year),
            )
            continue
        merged_intervals.append(next_interval)
    return merged_intervals


def _sum_interval_years(intervals: list[DurationInterval]) -> int:
    """Return inclusive years across merged closed intervals."""

    return sum(interval.end_year - interval.start_year + 1 for interval in intervals)


def _format_year_range(start_year: int, end_year: int) -> str:
    """Format a concise year range string for deterministic answers."""

    if start_year == end_year:
        return str(start_year)
    return f"{start_year}-{end_year}"
