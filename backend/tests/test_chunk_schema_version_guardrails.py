"""Guardrail tests that prevent hardcoded chunk-schema version constants in Python."""

from __future__ import annotations

import re
from pathlib import Path


CHUNK_SCHEMA_VERSION_ASSIGNMENT_PATTERN = re.compile(
    r"\bCHUNK_SCHEMA_VERSION\s*=\s*\d+\b"
)
SUPPORTED_CHUNK_SCHEMA_VERSION_ASSIGNMENT_PATTERN = re.compile(
    r"\bSUPPORTED_CHUNK_SCHEMA_VERSION\s*=\s*\d+\b"
)


def _iter_python_files(root_directory: Path) -> list[Path]:
    """Return all Python files under the backend tree for guardrail scanning.

    Inputs:
        root_directory: Backend repository directory containing source and tests.
    Outputs:
        Sorted list of Python file paths under ``root_directory``.
    Edge cases:
        Includes tests to prevent regressions from entering either runtime or test
        helper modules.
    Concurrency/atomicity:
        Pure file-system traversal with no mutation.
    """

    python_files: list[Path] = []
    for python_file_path in root_directory.rglob("*.py"):
        if not python_file_path.is_file():
            continue
        python_files.append(python_file_path)
    return sorted(python_files)


def _format_violation_line(path: Path, file_text: str, match: re.Match[str]) -> str:
    """Build a concise violation string with path, line number, and matched code.

    Inputs:
        path: Path to the violating file.
        file_text: Full text contents of the file.
        match: Regex match representing a hardcoded version assignment.
    Outputs:
        Human-readable string ``<path>:<line>: <snippet>``.
    Edge cases:
        Uses line-number calculation based on newline counting to avoid requiring
        external tooling.
    Concurrency/atomicity:
        Pure formatting helper with no side effects.
    """

    line_number = file_text.count("\n", 0, match.start()) + 1
    return f"{path}:{line_number}: {match.group(0)}"


def test_python_files_do_not_define_hardcoded_chunk_schema_version_constants() -> None:
    """Fail when Python files hardcode chunk schema version constants.

    Inputs:
        None.
    Outputs:
        None. Asserts there are no matching assignments in backend Python files.
    Edge cases:
        ``backend/schema/chunk.schema.json`` is intentionally not scanned because
        the allowed schema-version source of truth is JSON, not Python.
    Concurrency/atomicity:
        Read-only scan of repository files.
    """

    backend_root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for python_file_path in _iter_python_files(backend_root):
        file_text = python_file_path.read_text(encoding="utf-8")
        for assignment_pattern in (
            CHUNK_SCHEMA_VERSION_ASSIGNMENT_PATTERN,
            SUPPORTED_CHUNK_SCHEMA_VERSION_ASSIGNMENT_PATTERN,
        ):
            for assignment_match in assignment_pattern.finditer(file_text):
                violations.append(
                    _format_violation_line(
                        python_file_path.relative_to(backend_root),
                        file_text,
                        assignment_match,
                    )
                )

    assert not violations, (
        "Hardcoded chunk schema version constants are disallowed in Python files. "
        "Derive runtime support from backend/schema/chunk.schema.json. "
        f"Found: {'; '.join(violations)}"
    )
