"""Tests for duration-domain configuration validation behavior."""

from __future__ import annotations

import pytest

from api import duration_domain_config


def test_validate_families_rejects_unknown_member_labels() -> None:
    """Family members not listed in canonical_labels should raise ValueError."""
    raw_config: dict[str, object] = {
        "canonical_labels": ["devops"],
        "families": {
            "infra_ops": {
                "members": ["devops", "unknown_label"],
                "query_aliases": [],
            }
        },
    }

    with pytest.raises(ValueError, match="contains unknown label 'unknown_label'"):
        duration_domain_config._validate_families(  # pyright: ignore[reportPrivateUsage]
            raw_config,
            canonical_labels=["devops"],
        )
