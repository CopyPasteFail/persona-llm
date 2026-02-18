"""Load and validate duration-domain family configuration.

This module centralizes the mapping between fine-grained stint labels and
query-facing domain families used by deterministic duration routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DurationFamilyConfig:
    """Validated family definition for deterministic duration routing.

    Inputs:
    - members: Canonical fine-grained labels accepted for this family.
    - query_aliases: Lowercased aliases used to map free-text questions.

    Output:
    - Immutable family configuration consumed by the routing helpers.
    """

    members: frozenset[str]
    query_aliases: tuple[str, ...]


@dataclass(frozen=True)
class DurationDomainConfig:
    """Validated global config for deterministic duration routing.

    Inputs:
    - canonical_labels: Full set of allowed fine-grained stint labels.
    - families: Family definitions keyed by family name.

    Output:
    - Immutable config used by ingestion and query-time resolution.
    """

    canonical_labels: frozenset[str]
    families: Mapping[str, DurationFamilyConfig]


@lru_cache(maxsize=1)
def get_config() -> DurationDomainConfig:
    """Load and cache the validated duration-domain configuration.

    Inputs:
    - None. Reads the JSON file at ``backend/config/experience_domain_config.json``.

    Output:
    - Parsed and validated immutable ``DurationDomainConfig``.

    Edge cases:
    - Raises ``RuntimeError`` with explicit path context when file loading fails.
    - Raises ``ValueError`` for invalid label casing, unknown members, or duplicates.
    """

    config_path = Path(__file__).resolve().parent.parent / "config" / "experience_domain_config.json"
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - delegated to runtime/tests
        raise RuntimeError(
            f"Failed to load duration-domain config at {config_path}: {exc}"
        ) from exc

    canonical_labels = _validate_canonical_labels(raw_config)
    families = _validate_families(raw_config, canonical_labels)
    return DurationDomainConfig(
        canonical_labels=frozenset(canonical_labels),
        families=MappingProxyType(families),
    )


def resolve_families_for_query(question: str) -> list[str]:
    """Resolve matching family keys for a free-text question.

    Inputs:
    - question: User question text.

    Output:
    - Ordered list of matching family keys. May contain multiple families.

    Edge cases:
    - Empty or non-tokenizable questions return an empty list.
    - Matching is case-insensitive and punctuation-insensitive.
    """

    normalized_question = (question or "").strip().lower()
    question_tokens = _TOKEN_PATTERN.findall(normalized_question)
    if not question_tokens:
        return []

    token_set = set(question_tokens)
    normalized_phrase_text = " ".join(question_tokens)
    matches: list[str] = []

    config = get_config()
    for family_key, family_config in config.families.items():
        if _family_matches_question(
            family_config=family_config,
            normalized_phrase_text=normalized_phrase_text,
            token_set=token_set,
        ):
            matches.append(family_key)

    return matches


def accepted_labels_for_family(family_key: str) -> set[str]:
    """Return the accepted canonical labels for a configured family.

    Inputs:
    - family_key: Domain family key from the validated configuration.

    Output:
    - Set of canonical labels accepted for that family.

    Edge cases:
    - Raises ``KeyError`` for unknown family keys.
    """

    family_config = get_config().families[family_key]
    return set(family_config.members)


def _validate_canonical_labels(raw_config: Mapping[str, object]) -> list[str]:
    """Validate and normalize the canonical fine-grained label list.

    Inputs:
    - raw_config: Parsed config mapping from JSON.

    Output:
    - Ordered list of validated canonical labels.

    Edge cases:
    - Rejects empty lists, duplicate labels, and non-lowercase labels.
    """

    labels_raw = raw_config.get("canonical_labels")
    if not isinstance(labels_raw, list) or not labels_raw:
        raise ValueError("experience_domain_config.canonical_labels must be a non-empty list")

    labels: list[str] = []
    seen_labels: set[str] = set()
    for index, label_value in enumerate(labels_raw):
        if not isinstance(label_value, str) or not label_value.strip():
            raise ValueError(
                "experience_domain_config.canonical_labels contains an invalid "
                f"entry at index {index}"
            )
        normalized_label = label_value.strip()
        if normalized_label != normalized_label.lower():
            raise ValueError(
                "experience_domain_config.canonical_labels must be lowercase; "
                f"received '{label_value}'"
            )
        if normalized_label in seen_labels:
            raise ValueError(
                "experience_domain_config.canonical_labels contains duplicate "
                f"label '{normalized_label}'"
            )
        seen_labels.add(normalized_label)
        labels.append(normalized_label)

    return labels


def _validate_families(
    raw_config: Mapping[str, object],
    canonical_labels: list[str],
) -> dict[str, DurationFamilyConfig]:
    """Validate family mappings and query aliases.

    Inputs:
    - raw_config: Parsed config mapping from JSON.
    - canonical_labels: Previously validated canonical labels.

    Output:
    - Mapping of family key to immutable family configuration.

    Edge cases:
    - Rejects families with unknown members, duplicates, or non-lowercase aliases.
    """

    families_raw = raw_config.get("families")
    if not isinstance(families_raw, Mapping) or not families_raw:
        raise ValueError("experience_domain_config.families must be a non-empty object")

    canonical_label_set = set(canonical_labels)
    families: dict[str, DurationFamilyConfig] = {}
    for family_key_raw, family_value in families_raw.items():
        if not isinstance(family_key_raw, str) or not family_key_raw.strip():
            raise ValueError("experience_domain_config.families includes an empty family key")
        if family_key_raw != family_key_raw.lower():
            raise ValueError(
                "experience_domain_config.families keys must be lowercase; "
                f"received '{family_key_raw}'"
            )
        if not isinstance(family_value, Mapping):
            raise ValueError(
                f"experience_domain_config.families.{family_key_raw} must be an object"
            )

        members_raw = family_value.get("members")
        if not isinstance(members_raw, list) or not members_raw:
            raise ValueError(
                f"experience_domain_config.families.{family_key_raw}.members "
                "must be a non-empty list"
            )

        seen_members: set[str] = set()
        members: set[str] = set()
        for member_index, member_value in enumerate(members_raw):
            if not isinstance(member_value, str) or not member_value.strip():
                raise ValueError(
                    f"experience_domain_config.families.{family_key_raw}.members "
                    f"contains invalid entry at index {member_index}"
                )
            normalized_member = member_value.strip()
            if normalized_member != normalized_member.lower():
                raise ValueError(
                    f"experience_domain_config.families.{family_key_raw}.members "
                    "must be lowercase"
                )
            if normalized_member in seen_members:
                raise ValueError(
                    f"experience_domain_config.families.{family_key_raw}.members "
                    f"contains duplicate label '{normalized_member}'"
                )
            if normalized_member not in canonical_label_set:
                raise ValueError(
                    f"experience_domain_config.families.{family_key_raw}.members "
                    f"contains unknown label '{normalized_member}'"
                )
            seen_members.add(normalized_member)
            members.add(normalized_member)

        aliases_raw = family_value.get("query_aliases")
        if not isinstance(aliases_raw, list):
            raise ValueError(
                f"experience_domain_config.families.{family_key_raw}.query_aliases "
                "must be a list"
            )

        aliases = _normalize_query_aliases(
            aliases_raw,
            family_key=family_key_raw,
            members=members,
        )
        families[family_key_raw] = DurationFamilyConfig(
            members=frozenset(members),
            query_aliases=tuple(aliases),
        )

    return families


def _normalize_query_aliases(
    aliases_raw: list[object],
    *,
    family_key: str,
    members: set[str],
) -> list[str]:
    """Normalize and validate family query aliases.

    Inputs:
    - aliases_raw: Raw alias values from config.
    - family_key: Parent family key for error context.
    - members: Canonical family member labels.

    Output:
    - Ordered unique alias list (all lowercase).

    Edge cases:
    - Rejects non-string aliases, duplicates, and uppercase aliases.
    - Adds member labels and family-key variants for direct matches.
    """

    aliases: list[str] = []
    seen_aliases: set[str] = set()

    def append_alias(alias: str, *, reject_duplicates: bool = True) -> None:
        normalized_alias = alias.strip()
        if not normalized_alias:
            return
        if normalized_alias != normalized_alias.lower():
            raise ValueError(
                f"experience_domain_config.families.{family_key}.query_aliases "
                f"must be lowercase; received '{alias}'"
            )
        if normalized_alias in seen_aliases:
            if not reject_duplicates:
                return
            raise ValueError(
                f"experience_domain_config.families.{family_key}.query_aliases "
                f"contains duplicate alias '{normalized_alias}'"
            )
        seen_aliases.add(normalized_alias)
        aliases.append(normalized_alias)

    for alias_index, alias_value in enumerate(aliases_raw):
        if not isinstance(alias_value, str):
            raise ValueError(
                f"experience_domain_config.families.{family_key}.query_aliases "
                f"contains invalid entry at index {alias_index}"
            )
        append_alias(alias_value, reject_duplicates=True)

    append_alias(family_key, reject_duplicates=False)
    append_alias(family_key.replace("_", " "), reject_duplicates=False)
    for member in sorted(members):
        append_alias(member, reject_duplicates=False)
        append_alias(member.replace("_", " "), reject_duplicates=False)

    return aliases


def _family_matches_question(
    *,
    family_config: DurationFamilyConfig,
    normalized_phrase_text: str,
    token_set: set[str],
) -> bool:
    """Check whether any configured alias matches the tokenized question.

    Inputs:
    - family_config: Family config with normalized aliases.
    - normalized_phrase_text: Space-delimited normalized question tokens.
    - token_set: Set of normalized question tokens.

    Output:
    - True when at least one alias matches, else False.
    """

    for alias in family_config.query_aliases:
        alias_tokens = _TOKEN_PATTERN.findall(alias)
        if not alias_tokens:
            continue
        if len(alias_tokens) == 1:
            if alias_tokens[0] in token_set:
                return True
            continue
        alias_phrase = " ".join(alias_tokens)
        if alias_phrase and alias_phrase in normalized_phrase_text:
            return True
    return False
