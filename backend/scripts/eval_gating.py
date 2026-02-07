"""Replay one or more JSONL datasets through RAG and record call-gating metrics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, cast

# Ensure local imports resolve when run as a script from repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_OUTPUT_FILENAME_PREFIX = "gating_eval_output_"
DEFAULT_OUTPUT_FILENAME_EXTENSION = ".jsonl"
DEFAULT_DATASET_PATH = (
    BACKEND_ROOT.parent / "private-template" / "eval_datasets" / "sample_questions.jsonl"
)
DEFAULT_OUTPUT_DIRECTORY = Path("./out")
EVAL_ENABLE_THINKING_GATING = True
EVAL_ENABLE_LLM_CALL_GATING = True
ANSWER_HEAD_CHAR_LIMIT = 120
OUTPUT_SCHEMA_VERSION = "gating_eval_v3"
RECORD_TYPE_RUN_METADATA = "run_metadata"
RECORD_TYPE_DATASET_METADATA = "dataset_metadata"
RECORD_TYPE_QUESTION_RESULT = "question_result"
MODE_DETERMINISTIC = "deterministic"
MODE_VERTEX = "vertex"
MODE_INTEGRATED_RETRIEVAL_ONLY = "integrated_retrieval_only"
EXPECTED_LABEL_CALL = "CALL"
EXPECTED_LABEL_SKIP = "SKIP"
EXPECTED_LABEL_BORDERLINE = "BORDERLINE"
SUPPORTED_EXPECTED_LABELS = {
    EXPECTED_LABEL_CALL,
    EXPECTED_LABEL_SKIP,
    EXPECTED_LABEL_BORDERLINE,
}
DETERMINISTIC_CHUNK_ID = "mock:1"
DETERMINISTIC_CHUNKS: list[dict[str, Any]] = [
    {
        "id": DETERMINISTIC_CHUNK_ID,
        "text": "deterministic mock chunk",
        "metadata": {},
    }
]
DETERMINISTIC_REQUIRED_ENV_DEFAULTS: dict[str, str] = {
    "PERSONA_NAME": "Eval Persona",
    "PROJECT_ID": "local-eval-project",
    "REGION": "us-central1",
    "LLM_BACKEND": MODE_DETERMINISTIC,
    "API_KEY": "local-eval-api-key",
    "MAX_OUTPUT_TOKENS": "256",
    "REQ_TIMEOUT_MS": "10000",
    "BUCKET_NAME": "local-eval-bucket",
}


@dataclass(frozen=True)
class DatasetQuestionRow:
    """Single question row loaded from a JSONL evaluation dataset.

    Inputs:
    - id: Stable row id used for output traceability.
    - question: Natural language question to replay through the RAG pipeline.
    - expected: Optional expected gating label (`CALL`, `SKIP`, `BORDERLINE`).
    - notes: Optional notes column copied from dataset metadata.

    Outputs:
    - Immutable record consumed by evaluation execution.

    Edge cases:
    - `expected` may be None when unlabeled rows are used for exploratory runs.
    - `notes` is ignored by execution and only preserved for operator context.

    Concurrency/atomicity:
    - Immutable value object; safe to share across concurrent readers.
    """

    id: str
    question: str
    expected: str | None
    notes: str | None


@dataclass(frozen=True)
class EvaluationRuntime:
    """Runtime bundle for orchestrator-based evaluation execution.

    Inputs:
    - mode: Execution mode (`deterministic`, `vertex`, or `integrated_retrieval_only`).
    - retrieval_module: Configured retrieval module with embedding/vector/chunks.
    - llm_backend: Optional LLM backend implementation selected for this run.
    - settings: Loaded backend settings object.
    - rag_chat_orchestrator: Orchestrator module exposing `run_rag_chat`.

    Outputs:
    - Immutable runtime dependencies passed into per-row evaluations.

    Edge cases:
    - The retrieval module is expected to be preconfigured before row execution.

    Concurrency/atomicity:
    - Immutable container; module-level retrieval state should be configured once
      before parallel calls.
    """

    mode: str
    retrieval_module: Any
    llm_backend: Any | None
    settings: Any
    rag_chat_orchestrator: Any


@dataclass(frozen=True)
class EvalCliOverrides:
    """CLI threshold/top-k overrides captured for run metadata output.

    Inputs:
    - weighted_score_threshold: Optional override for weighted-score gate threshold.
    - bm25_score_threshold: Optional override for BM25 gate threshold.
    - top_k: Optional override for retrieval candidate depth.

    Outputs:
    - Immutable override snapshot used by metadata row builders.

    Edge cases:
    - Fields remain None when caller does not provide CLI overrides.

    Concurrency/atomicity:
    - Immutable value object; safe for read-only use across all rows.
    """

    weighted_score_threshold: float | None
    bm25_score_threshold: float | None
    top_k: int | None


@dataclass(frozen=True)
class EffectiveEvalSettings:
    """Effective evaluation settings after applying CLI overrides to defaults.

    Inputs:
    - top_k: Effective retrieval candidate depth.
    - weighted_score_threshold: Effective weighted-score gate threshold.
    - bm25_score_threshold: Effective BM25 gate threshold.
    - retrieval_vector_weight: Effective vector score blend weight.
    - retrieval_bm25_weight: Effective BM25 score blend weight.
    - vector_backend: Effective vector backend identifier.
    - llm_backend: Effective LLM backend identifier.
    - enable_llm_call_gating: Whether call-gating was enabled for this run.
    - enable_thinking_gating: Whether thinking-gating was enabled for this run.

    Outputs:
    - Immutable settings snapshot used by metadata rows and row execution.

    Edge cases:
    - Effective threshold/top-k values include CLI overrides when provided.

    Concurrency/atomicity:
    - Immutable value object; safe for read-only use across all rows.
    """

    top_k: int
    weighted_score_threshold: float
    bm25_score_threshold: float
    retrieval_vector_weight: float
    retrieval_bm25_weight: float
    vector_backend: str
    llm_backend: str
    enable_llm_call_gating: bool
    enable_thinking_gating: bool


class DeterministicEmbeddingClient:
    """Deterministic embedding stub that always returns the same embedding."""

    def embed(self, text: str) -> list[float] | None:
        return [1.0]


class DeterministicVectorClient:
    """Deterministic vector search stub that always returns the same candidate."""

    def query(self, embedding: Sequence[float], *, top_k: int) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        return [{"id": DETERMINISTIC_CHUNK_ID, "distance": 0.0}]


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for gating evaluation runs.

    Inputs:
    - None.

    Outputs:
    - Configured `argparse.ArgumentParser` instance.

    Edge cases:
    - This evaluation intentionally always enables both thinking-gating and
      llm-gating, so no gate-enable CLI flags are exposed.

    Concurrency/atomicity:
    - Pure parser construction with no shared-state mutation.
    """

    parser = argparse.ArgumentParser(
        description="Replay dataset questions through RAG and collect llm-gating metrics."
    )
    parser.add_argument(
        "--dataset",
        help=(
            "Path to JSONL question dataset file or directory. "
            "Supports absolute or relative paths."
        ),
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT_DIRECTORY),
        help=(
            "Output JSONL file path or directory. "
            "If a directory is provided, filename is auto-generated as "
            "gating_eval_output_YYYY-MM-DD_HH-MM-SS.jsonl. Default: ./out"
        ),
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        help="Optional limit on number of rows to evaluate from the dataset.",
    )
    parser.add_argument(
        "--mode",
        choices=[MODE_DETERMINISTIC, MODE_VERTEX, MODE_INTEGRATED_RETRIEVAL_ONLY],
        default=MODE_INTEGRATED_RETRIEVAL_ONLY,
        help=(
            "Runtime wiring mode. Default: deterministic (offline, no Vertex calls). "
            "integrated_retrieval_only uses integrated retrieval path and skips LLM calls."
        ),
    )
    parser.add_argument(
        "--weighted-score-threshold",
        type=float,
        help="Optional override for weighted-score threshold.",
    )
    parser.add_argument(
        "--bm25-score-threshold",
        type=float,
        help="Optional override for BM25 score threshold.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        help="Optional override for retrieval top-k candidate depth.",
    )
    return parser


def _resolve_dataset_path(dataset_argument: str | None) -> Path:
    """Resolve dataset path from CLI argument or template fallback.

    Inputs:
    - dataset_argument: Optional `--dataset` CLI value.

    Outputs:
    - Resolved filesystem path for the dataset file or dataset directory.

    Edge cases:
    - `~` paths are expanded.
    - Relative paths are interpreted relative to the current process working directory.
    - When no dataset argument is provided, template sample dataset path is returned.

    Concurrency/atomicity:
    - Pure path resolution with no side effects.
    """

    if dataset_argument:
        return Path(dataset_argument).expanduser()

    return DEFAULT_DATASET_PATH


def _load_dataset_rows(dataset_path: Path, max_rows: int | None) -> list[DatasetQuestionRow]:
    """Load and validate JSONL question rows from disk.

    Inputs:
    - dataset_path: Path to JSONL dataset file.
    - max_rows: Optional maximum number of non-empty rows to return.

    Outputs:
    - List of validated `DatasetQuestionRow` records.

    Edge cases:
    - Blank lines are ignored.
    - Missing `expected` values are accepted.
    - Parsing/shape errors include the failing line number for quick debugging.

    Concurrency/atomicity:
    - Read-only file operation; no shared-state mutation.
    """

    rows: list[DatasetQuestionRow] = []
    with dataset_path.open("r", encoding="utf-8") as dataset_handle:
        for line_number, raw_line in enumerate(dataset_handle, start=1):
            raw_line_stripped = raw_line.strip()
            if not raw_line_stripped:
                continue

            try:
                payload_raw: object = json.loads(raw_line_stripped)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON in dataset at line {line_number}: {error.msg}"
                ) from error

            if not isinstance(payload_raw, dict):
                raise ValueError(
                    f"Invalid dataset row at line {line_number}: expected JSON object."
                )

            payload_object = cast(dict[object, object], payload_raw)
            payload: dict[str, object] = {}
            for payload_key, payload_value in payload_object.items():
                if not isinstance(payload_key, str):
                    raise ValueError(
                        f"Invalid dataset row at line {line_number}: JSON object keys must be strings."
                    )
                payload[payload_key] = payload_value

            question_id_raw: object | None = payload.get("id")
            question_text_raw: object | None = payload.get("question")
            question_id = str(question_id_raw or "").strip()
            question_text = str(question_text_raw or "").strip()
            if not question_id:
                raise ValueError(
                    f"Invalid dataset row at line {line_number}: field 'id' is required."
                )
            if not question_text:
                raise ValueError(
                    f"Invalid dataset row at line {line_number}: field 'question' is required."
                )

            expected_value_raw: object | None = payload.get("expected")
            expected_value: str | None = None
            if expected_value_raw is not None and str(expected_value_raw).strip():
                expected_value = str(expected_value_raw).strip().upper()

            notes_value_raw: object | None = payload.get("notes")
            notes_value: str | None = None
            if notes_value_raw is not None and str(notes_value_raw).strip():
                notes_value = str(notes_value_raw).strip()

            rows.append(
                DatasetQuestionRow(
                    id=question_id,
                    question=question_text,
                    expected=expected_value,
                    notes=notes_value,
                )
            )

            if max_rows is not None and len(rows) >= max_rows:
                break

    return rows


def _resolve_input_dataset_files(resolved_dataset_path: Path) -> list[Path]:
    """Resolve one or more dataset files from a file-or-directory input.

    Inputs:
    - resolved_dataset_path: Path resolved by `_resolve_dataset_path`.

    Outputs:
    - List of dataset JSONL file paths to evaluate, in deterministic order.

    Edge cases:
    - When a directory contains no `*.jsonl` files, this returns an empty list.
      The caller can fall back to the template dataset in that case.
    - Non-file, non-directory paths return an empty list.

    Concurrency/atomicity:
    - Read-only filesystem enumeration; no shared-state mutation.
    """

    if resolved_dataset_path.is_dir():
        return sorted(resolved_dataset_path.glob("*.jsonl"))
    if resolved_dataset_path.is_file():
        return [resolved_dataset_path]
    return []


def _is_jsonl_output_path(path: Path) -> bool:
    """Check whether a path looks like an explicit JSONL file output target.

    Inputs:
    - path: Candidate output path from CLI.

    Outputs:
    - True when the path suffix is `.jsonl`; False otherwise.

    Edge cases:
    - Suffix comparison is case-insensitive.

    Concurrency/atomicity:
    - Pure path inspection helper.
    """

    return path.suffix.lower() == DEFAULT_OUTPUT_FILENAME_EXTENSION


def _resolve_output_path(output_argument: str) -> Path:
    """Resolve and validate output target from CLI input.

    Inputs:
    - output_argument: Raw `--out` CLI value.

    Outputs:
    - Output JSONL file path.

    Edge cases:
    - Paths ending with `.jsonl` are treated as explicit file targets.
    - Non-`.jsonl` paths are treated as directories and receive an auto-generated filename.
    - Existing directories cannot be used as explicit `.jsonl` file paths.

    Concurrency/atomicity:
    - Pure path validation/generation without filesystem writes.
    """

    output_target = Path(output_argument).expanduser()
    if _is_jsonl_output_path(output_target):
        if output_target.exists() and output_target.is_dir():
            raise ValueError("--out points to an existing directory; provide a .jsonl file path.")
        return output_target

    if output_target.exists() and output_target.is_file():
        raise ValueError(
            "--out points to an existing file without a .jsonl suffix; provide a directory or .jsonl file."
        )
    return _build_output_path(output_target)


def _build_output_path(output_directory: Path) -> Path:
    """Build timestamped output file path inside the configured output directory.

    Inputs:
    - output_directory: Directory path chosen by `_resolve_output_path`.

    Outputs:
    - Timestamped output JSONL path in the format
      `gating_eval_output_YYYY-MM-DD_HH-MM-SS.jsonl`.

    Edge cases:
    - If the generated filename already exists, `_2`, `_3`, ... is appended.

    Concurrency/atomicity:
    - Pure path generation without filesystem writes.
    """

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_filename = (
        f"{DEFAULT_OUTPUT_FILENAME_PREFIX}{timestamp}{DEFAULT_OUTPUT_FILENAME_EXTENSION}"
    )
    output_path = output_directory / base_filename
    if not output_path.exists():
        return output_path

    suffix_index = 2
    while True:
        candidate_filename = (
            f"{DEFAULT_OUTPUT_FILENAME_PREFIX}{timestamp}_{suffix_index}"
            f"{DEFAULT_OUTPUT_FILENAME_EXTENSION}"
        )
        candidate_output_path = output_directory / candidate_filename
        if not candidate_output_path.exists():
            return candidate_output_path
        suffix_index += 1


def _utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    Inputs:
    - None.

    Outputs:
    - UTC timestamp string (e.g., `2026-02-10T14:23:45+00:00`).

    Edge cases:
    - Relies on process clock; caller should not assume monotonic behavior.

    Concurrency/atomicity:
    - Pure time-read helper with no side effects.
    """

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _apply_deterministic_required_env_defaults() -> None:
    """Set minimal environment defaults required to load backend settings.

    Inputs:
    - None.

    Outputs:
    - None. Mutates process environment only for missing keys.

    Edge cases:
    - Existing environment values are preserved (`setdefault`).
    - Defaults are intended for local deterministic/offline evaluation only.

    Concurrency/atomicity:
    - Process-global env mutation should happen once before importing backend
      modules in this script.
    """

    for env_name, env_value in DETERMINISTIC_REQUIRED_ENV_DEFAULTS.items():
        os.environ.setdefault(env_name, env_value)


def _initialize_integrated_retrieval_runtime(retrieval_module: Any, settings: Any) -> None:
    """Configure retrieval wiring exactly as integrated API startup does.

    Inputs:
    - retrieval_module: Retrieval module exposing embedding/vector/chunk setup functions.
    - settings: Loaded backend settings object with project/region values.

    Outputs:
    - None. Configures module-level retrieval dependencies for this process.

    Edge cases:
    - Raises when dataset cache cannot be loaded or when embedding client setup fails.

    Concurrency/atomicity:
    - Intended for one-time initialization before row evaluation begins.
    """

    from api import runtime_wiring

    runtime_wiring.configure_integrated_retrieval_runtime(
        retrieval_module=retrieval_module,
        project_id=settings.PROJECT_ID,
        region=settings.REGION,
    )


def _initialize_runtime(mode: str) -> EvaluationRuntime:
    """Initialize orchestrator runtime dependencies for the selected mode.

    Inputs:
    - mode: Execution mode (`deterministic`, `vertex`, or `integrated_retrieval_only`).

    Outputs:
    - `EvaluationRuntime` with configured retrieval/LLM dependencies.

    Edge cases:
    - Deterministic mode configures in-memory deterministic clients/chunks.
    - Vertex mode configures integrated retrieval wiring plus a real LLM backend.
    - Integrated retrieval-only mode configures integrated retrieval wiring and
      intentionally skips LLM backend initialization.

    Concurrency/atomicity:
    - Configures module-level retrieval clients/chunks once for the run.
    """

    if mode == MODE_DETERMINISTIC:
        _apply_deterministic_required_env_defaults()

    from api import rag_chat_orchestrator, retrieval
    from api.settings import settings

    if mode == MODE_DETERMINISTIC:
        from api import llm_backends

        retrieval.configure_embedding_client(DeterministicEmbeddingClient())
        retrieval.configure_vector_client(DeterministicVectorClient())
        retrieval.configure_chunk_store(DETERMINISTIC_CHUNKS)
        llm_backend = llm_backends.DeterministicLlmBackend()
        return EvaluationRuntime(
            mode=mode,
            retrieval_module=retrieval,
            llm_backend=llm_backend,
            settings=settings,
            rag_chat_orchestrator=rag_chat_orchestrator,
        )

    if mode == MODE_VERTEX:
        from api import llm_backends

        _initialize_integrated_retrieval_runtime(retrieval, settings)
        llm_backend = llm_backends.get_llm_backend(default_backend=MODE_VERTEX)
        return EvaluationRuntime(
            mode=mode,
            retrieval_module=retrieval,
            llm_backend=llm_backend,
            settings=settings,
            rag_chat_orchestrator=rag_chat_orchestrator,
        )

    if mode == MODE_INTEGRATED_RETRIEVAL_ONLY:
        _initialize_integrated_retrieval_runtime(retrieval, settings)
        return EvaluationRuntime(
            mode=mode,
            retrieval_module=retrieval,
            llm_backend=None,
            settings=settings,
            rag_chat_orchestrator=rag_chat_orchestrator,
        )

    raise ValueError(f"Unsupported mode: {mode}")


def _sanitize_answer_head(answer_text: str) -> str:
    """Build a one-line answer preview with bounded length.

    Inputs:
    - answer_text: Full model answer.

    Outputs:
    - First `ANSWER_HEAD_CHAR_LIMIT` characters after newline flattening.

    Edge cases:
    - Empty answers return an empty preview string.
    - Multiple lines are joined with single spaces to keep one-line output.

    Concurrency/atomicity:
    - Pure string transformation with no side effects.
    """

    one_line_answer = " ".join(answer_text.splitlines())
    return one_line_answer[:ANSWER_HEAD_CHAR_LIMIT]


def _resolve_effective_thresholds(
    runtime: EvaluationRuntime,
    *,
    weighted_score_threshold_override: float | None,
    bm25_score_threshold_override: float | None,
) -> tuple[float, float]:
    """Resolve effective llm-gating thresholds for a single evaluation row.

    Inputs:
    - runtime: Runtime containing baseline settings thresholds.
    - weighted_score_threshold_override: Optional CLI override for weighted-score threshold.
    - bm25_score_threshold_override: Optional CLI override for BM25 threshold.

    Outputs:
    - Tuple of `(weighted_score_threshold, bm25_score_threshold)` used for evaluation.

    Edge cases:
    - Missing overrides fall back to settings values.

    Concurrency/atomicity:
    - Pure value resolution helper.
    """

    weighted_score_threshold_effective = (
        runtime.settings.WEIGHTED_SCORE_THRESHOLD
        if weighted_score_threshold_override is None
        else weighted_score_threshold_override
    )
    bm25_score_threshold_effective = (
        runtime.settings.BM25_SCORE_THRESHOLD
        if bm25_score_threshold_override is None
        else bm25_score_threshold_override
    )
    return weighted_score_threshold_effective, bm25_score_threshold_effective


def _resolve_effective_top_k(
    runtime: EvaluationRuntime,
    *,
    top_k_override: int | None,
) -> int:
    """Resolve effective retrieval top-k for a single evaluation row.

    Inputs:
    - runtime: Runtime containing baseline settings top-k.
    - top_k_override: Optional CLI override for retrieval top-k.

    Outputs:
    - Integer top-k used for retrieval candidate depth.

    Edge cases:
    - Missing override falls back to settings value.

    Concurrency/atomicity:
    - Pure value resolution helper.
    """

    return runtime.settings.TOP_K if top_k_override is None else top_k_override


def _resolve_runtime_backend_labels(runtime: EvaluationRuntime) -> tuple[str, str]:
    """Resolve effective `(vector_backend, llm_backend)` labels from runtime wiring.

    Inputs:
    - runtime: Initialized evaluation runtime containing selected mode and wired clients.

    Outputs:
    - Tuple `(vector_backend_label, llm_backend_label)` used for metadata reporting.

    Edge cases:
    - Deterministic mode always reports deterministic labels regardless of env settings.
    - Integrated-retrieval-only mode reports `llm_backend` as `none` because no LLM is wired.
    - Unknown LLM backend class names fall back to lowercased class name.

    Concurrency/atomicity:
    - Pure read-only helper; no shared-state mutation.
    """

    if runtime.mode == MODE_DETERMINISTIC:
        return ("deterministic", MODE_DETERMINISTIC)

    vector_backend_label = str(runtime.settings.VECTOR_BACKEND).strip().lower() or "unknown"
    if runtime.mode == MODE_INTEGRATED_RETRIEVAL_ONLY or runtime.llm_backend is None:
        return (vector_backend_label, "none")

    llm_backend_class_name = runtime.llm_backend.__class__.__name__.strip()
    if llm_backend_class_name == "DeterministicLlmBackend":
        llm_backend_label = MODE_DETERMINISTIC
    elif llm_backend_class_name == "VertexLlmBackend":
        llm_backend_label = MODE_VERTEX
    else:
        llm_backend_label = llm_backend_class_name.lower() or "unknown"

    return (vector_backend_label, llm_backend_label)


def _resolve_effective_eval_settings(
    runtime: EvaluationRuntime,
    *,
    weighted_score_threshold_override: float | None,
    bm25_score_threshold_override: float | None,
    top_k_override: int | None,
) -> EffectiveEvalSettings:
    """Resolve effective evaluation settings after applying CLI overrides.

    Inputs:
    - runtime: Runtime containing baseline backend settings values.
    - weighted_score_threshold_override: Optional weighted-score threshold override.
    - bm25_score_threshold_override: Optional BM25 threshold override.
    - top_k_override: Optional retrieval top-k override.

    Outputs:
    - `EffectiveEvalSettings` used for metadata emission and row execution.

    Edge cases:
    - Override values take precedence over loaded settings.

    Concurrency/atomicity:
    - Pure value construction helper; no shared-state mutation.
    """

    weighted_score_threshold_effective, bm25_score_threshold_effective = (
        _resolve_effective_thresholds(
            runtime,
            weighted_score_threshold_override=weighted_score_threshold_override,
            bm25_score_threshold_override=bm25_score_threshold_override,
        )
    )
    top_k_effective = _resolve_effective_top_k(
        runtime,
        top_k_override=top_k_override,
    )
    vector_backend_label, llm_backend_label = _resolve_runtime_backend_labels(runtime)

    return EffectiveEvalSettings(
        top_k=top_k_effective,
        weighted_score_threshold=weighted_score_threshold_effective,
        bm25_score_threshold=bm25_score_threshold_effective,
        retrieval_vector_weight=float(runtime.settings.RETRIEVAL_VECTOR_WEIGHT),
        retrieval_bm25_weight=float(runtime.settings.RETRIEVAL_BM25_WEIGHT),
        vector_backend=vector_backend_label,
        llm_backend=llm_backend_label,
        enable_llm_call_gating=EVAL_ENABLE_LLM_CALL_GATING,
        enable_thinking_gating=EVAL_ENABLE_THINKING_GATING,
    )


def _effective_settings_to_payload(
    effective_settings: EffectiveEvalSettings,
) -> dict[str, Any]:
    """Convert effective settings snapshot into JSON-serializable payload.

    Inputs:
    - effective_settings: Resolved effective settings snapshot.

    Outputs:
    - Dict payload for JSONL metadata rows.

    Edge cases:
    - None.

    Concurrency/atomicity:
    - Pure serialization helper.
    """

    return {
        "top_k": effective_settings.top_k,
        "weighted_score_threshold": effective_settings.weighted_score_threshold,
        "bm25_score_threshold": effective_settings.bm25_score_threshold,
        "retrieval_vector_weight": effective_settings.retrieval_vector_weight,
        "retrieval_bm25_weight": effective_settings.retrieval_bm25_weight,
        "vector_backend": effective_settings.vector_backend,
        "llm_backend": effective_settings.llm_backend,
        "enable_llm_call_gating": effective_settings.enable_llm_call_gating,
        "enable_thinking_gating": effective_settings.enable_thinking_gating,
    }


def _build_run_metadata_row(
    runtime: EvaluationRuntime,
    *,
    dataset_argument: str | None,
    dataset_path: Path,
    dataset_files: Sequence[Path],
    output_path: Path,
    max_rows: int | None,
    effective_settings: EffectiveEvalSettings,
) -> dict[str, Any]:
    """Build top-level run metadata row for JSONL output.

    Inputs:
    - runtime: Runtime containing loaded settings and selected mode.
    - dataset_argument: Raw CLI `--dataset` argument (if any).
    - dataset_path: Resolved dataset path used for this run.
    - dataset_files: Ordered dataset files evaluated in this run.
    - output_path: Resolved output JSONL target path.
    - max_rows: Optional row-cap CLI value.
    - effective_settings: Effective settings used for retrieval and gating.

    Outputs:
    - JSON-serializable run metadata record.

    Edge cases:
    - When a directory input resolves to multiple files, all are listed in
      `dataset_files`.

    Concurrency/atomicity:
    - Pure metadata construction helper.
    """
    weighted_consensus_count = int(
        getattr(runtime.rag_chat_orchestrator, "MIN_WEIGHTED_CONSENSUS_COUNT", 2)
    )
    settings_used_payload = _effective_settings_to_payload(effective_settings)
    settings_used_payload["weighted_consensus_count"] = weighted_consensus_count

    return {
        "record_type": RECORD_TYPE_RUN_METADATA,
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "mode": runtime.mode,
        "dataset_argument": dataset_argument,
        "dataset_path": str(dataset_path),
        "dataset_files": [str(dataset_file) for dataset_file in dataset_files],
        "output_path": str(output_path),
        "max_rows": max_rows,
        "settings_used": settings_used_payload,
    }


def _build_dataset_metadata_row(
    dataset_file: Path,
    *,
    row_count: int,
) -> dict[str, Any]:
    """Build per-dataset metadata row for JSONL output.

    Inputs:
    - dataset_file: Source dataset file path.
    - row_count: Number of dataset rows loaded from this file.

    Outputs:
    - JSON-serializable dataset metadata record.

    Edge cases:
    - `row_count` can be zero when an input file has only blank lines.

    Concurrency/atomicity:
    - Pure metadata construction helper.
    """

    return {
        "record_type": RECORD_TYPE_DATASET_METADATA,
        "dataset_file": str(dataset_file),
        "row_count": row_count,
    }


def _build_orchestrator_result_row(
    runtime: EvaluationRuntime,
    dataset_row: DatasetQuestionRow,
    *,
    effective_settings: EffectiveEvalSettings,
) -> dict[str, Any]:
    """Execute one question via full orchestrator and build output row.

    Inputs:
    - runtime: Runtime configured with retrieval and LLM backend.
    - dataset_row: Question row to evaluate.
    - effective_settings: Effective top-k and gate thresholds for this run.

    Outputs:
    - Result row including LLM gating metrics plus usage or answer preview fields.

    Edge cases:
    - If retrieval has no call-worthy retrieval signal, orchestrator returns the no-signal response and
      usage fallbacks; this row still includes gating metrics.

    Concurrency/atomicity:
    - Stateless orchestration call with no side effects beyond API calls.
    """

    if runtime.llm_backend is None:
        raise RuntimeError("Orchestrator evaluation mode requires an LLM backend.")

    start_time = time.perf_counter()
    chat_result = runtime.rag_chat_orchestrator.run_rag_chat(
        dataset_row.question,
        retrieval=runtime.retrieval_module,
        llm_backend=runtime.llm_backend,
        top_k=effective_settings.top_k,
        persona_name=runtime.settings.PERSONA_NAME,
        max_input_tokens=runtime.settings.MAX_INPUT_TOKENS,
        max_output_tokens=runtime.settings.MAX_OUTPUT_TOKENS,
        enable_thinking_gating=EVAL_ENABLE_THINKING_GATING,
        default_thinking_budget_tokens=runtime.settings.THINKING_BUDGET_TOKENS,
        enable_llm_call_gating=EVAL_ENABLE_LLM_CALL_GATING,
        weighted_score_threshold=effective_settings.weighted_score_threshold,
        bm25_score_threshold=effective_settings.bm25_score_threshold,
    )
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    response = chat_result.response
    answer_text = response.answer or ""
    selected_chunk_ids = [
        citation.id for citation in response.citations if str(citation.id or "").strip()
    ]

    usage_total_tokens: int | None = chat_result.usage_detail.get("total_tokens")
    finish_reason: str | None = chat_result.usage_detail.get("finish_reason")

    result_row: dict[str, Any] = {
        "record_type": RECORD_TYPE_QUESTION_RESULT,
        "id": dataset_row.id,
        "question": dataset_row.question,
        "mode": runtime.mode,
        "elapsed_ms": elapsed_ms,
        "would_call_llm_if_gated": chat_result.would_call_llm_if_gated,
        "llm_gate_reason": chat_result.llm_gate_reason,
        "top1_weighted_score": chat_result.top1_weighted_score,
        "top1_bm25_score": chat_result.top1_bm25_score,
        "top1_vector_score": chat_result.top1_vector_score,
        "best_weighted_score": chat_result.best_weighted_score,
        "best_bm25_score": chat_result.best_bm25_score,
        "selected_chunk_ids": selected_chunk_ids,
        "usage_input_tokens": response.usage.input_tokens,
        "usage_output_tokens": response.usage.output_tokens,
        "usage_thoughts_tokens": response.usage.thoughts_tokens,
        "usage_total_tokens": usage_total_tokens,
        "finish_reason": finish_reason,
        "answer_length_chars": len(answer_text),
        "answer_head": _sanitize_answer_head(answer_text),
    }
    if dataset_row.expected is not None:
        result_row["expected"] = dataset_row.expected

    return result_row


def _build_result_row(
    runtime: EvaluationRuntime,
    dataset_row: DatasetQuestionRow,
    *,
    effective_settings: EffectiveEvalSettings,
) -> dict[str, Any]:
    """Execute one question and build a JSON-safe result row for the selected mode.

    Inputs:
    - runtime: Preconfigured evaluation runtime dependencies.
    - dataset_row: Question row to evaluate.
    - effective_settings: Effective top-k and gate thresholds for this run.

    Outputs:
    - Dict suitable for JSONL output with gating metrics.

    Edge cases:
    - `integrated_retrieval_only` never calls LLM generation and writes
      retrieval-only metrics (ids and numeric fields).
    - Other modes preserve existing orchestrator-driven result fields.

    Concurrency/atomicity:
    - Stateless execution helper; side effects depend on selected mode.
    """

    if runtime.mode != MODE_INTEGRATED_RETRIEVAL_ONLY:
        return _build_orchestrator_result_row(
            runtime,
            dataset_row,
            effective_settings=effective_settings,
        )

    start_time = time.perf_counter()
    normalized_question = runtime.retrieval_module.normalize_question_for_first_person(
        (dataset_row.question or "").strip()
    )
    query_embedding = runtime.retrieval_module.embed_query(normalized_question)
    candidate_chunks = runtime.retrieval_module.search_vector_store(
        query_embedding,
        top_k=effective_settings.top_k,
    )
    selected_chunks = runtime.retrieval_module.apply_filters_and_boosting(candidate_chunks)
    gate_shadow_decision = runtime.rag_chat_orchestrator.compute_llm_gate_decision(
        selected_chunks,
        weighted_score_threshold=effective_settings.weighted_score_threshold,
        bm25_score_threshold=effective_settings.bm25_score_threshold,
    )
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    selected_chunk_ids = [
        str(chunk.get("id") or "").strip()
        for chunk in selected_chunks
        if str(chunk.get("id") or "").strip()
    ]

    result_row: dict[str, Any] = {
        "record_type": RECORD_TYPE_QUESTION_RESULT,
        "id": dataset_row.id,
        "question": dataset_row.question,
        "mode": MODE_INTEGRATED_RETRIEVAL_ONLY,
        "elapsed_ms": elapsed_ms,
        "would_call_llm_if_gated": gate_shadow_decision.would_call_llm,
        "llm_gate_reason": gate_shadow_decision.reason,
        "top1_weighted_score": gate_shadow_decision.top1_weighted_score,
        "top1_bm25_score": gate_shadow_decision.top1_bm25_score,
        "top1_vector_score": gate_shadow_decision.top1_vector_score,
        "best_weighted_score": gate_shadow_decision.best_weighted_score,
        "best_bm25_score": gate_shadow_decision.best_bm25_score,
        "selected_chunk_ids": selected_chunk_ids,
        "selected_count": len(selected_chunk_ids),
        "candidates_count": len(candidate_chunks),
    }
    if dataset_row.expected is not None:
        result_row["expected"] = dataset_row.expected

    return result_row


def _write_results_jsonl(output_path: Path, result_rows: Sequence[dict[str, Any]]) -> None:
    """Write result rows as JSONL and create parent directories as needed.

    Inputs:
    - output_path: Destination JSONL path.
    - result_rows: Sequence of JSON-serializable per-question result rows.

    Outputs:
    - None. Writes to filesystem.

    Edge cases:
    - Parent directories are created when missing.
    - Existing files are overwritten.

    Concurrency/atomicity:
    - Single writer append-free write; not atomic across crashes.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_handle:
        for result_row in result_rows:
            output_handle.write(json.dumps(result_row, ensure_ascii=True))
            output_handle.write("\n")


def _format_rate(numerator: int, denominator: int) -> str:
    """Format a rate as `XX.XX% (numerator/denominator)` with zero guard.

    Inputs:
    - numerator: Count of matching rows.
    - denominator: Population size for the metric.

    Outputs:
    - Human-readable metric string.

    Edge cases:
    - Denominator zero reports `n/a` while still showing raw counts.

    Concurrency/atomicity:
    - Pure formatting helper.
    """

    if denominator <= 0:
        return f"n/a ({numerator}/{denominator})"
    percentage = (numerator / denominator) * 100.0
    return f"{percentage:.2f}% ({numerator}/{denominator})"


def _print_summary(result_rows: Sequence[dict[str, Any]]) -> None:
    """Print aggregate counts, error metrics, and gate-reason distribution.

    Inputs:
    - result_rows: Evaluated result rows.

    Outputs:
    - None. Writes summary to stdout.

    Edge cases:
    - If no rows have expected labels, false-skip/call metrics are omitted.
    - Unsupported expected labels are ignored for error-rate denominators.

    Concurrency/atomicity:
    - Read-only aggregation with no shared-state mutation.
    """

    total_rows = len(result_rows)
    rows_with_expected = [
        row for row in result_rows if str(row.get("expected") or "").strip()
    ]
    rows_with_supported_expected = [
        row for row in rows_with_expected if row.get("expected") in SUPPORTED_EXPECTED_LABELS
    ]
    with_expected_count = len(rows_with_expected)
    with_supported_expected_count = len(rows_with_supported_expected)
    missing_expected_count = total_rows - with_expected_count
    unsupported_expected_count = with_expected_count - with_supported_expected_count

    print(f"total={total_rows}")
    print(f"with_expected={with_expected_count}")
    print(f"missing_expected={missing_expected_count}")
    if unsupported_expected_count > 0:
        print(f"unsupported_expected_labels={unsupported_expected_count}")

    if with_supported_expected_count > 0:
        expected_call_or_borderline_count = sum(
            1
            for row in rows_with_supported_expected
            if row.get("expected") in {EXPECTED_LABEL_CALL, EXPECTED_LABEL_BORDERLINE}
        )
        expected_skip_count = sum(
            1
            for row in rows_with_supported_expected
            if row.get("expected") == EXPECTED_LABEL_SKIP
        )
        false_skips = sum(
            1
            for row in rows_with_supported_expected
            if row.get("expected") in {EXPECTED_LABEL_CALL, EXPECTED_LABEL_BORDERLINE}
            and not bool(row.get("would_call_llm_if_gated"))
        )
        false_calls = sum(
            1
            for row in rows_with_supported_expected
            if row.get("expected") == EXPECTED_LABEL_SKIP
            and bool(row.get("would_call_llm_if_gated"))
        )

        print(
            "false_skips="
            + _format_rate(false_skips, expected_call_or_borderline_count)
        )
        print("false_calls=" + _format_rate(false_calls, expected_skip_count))

    reason_counts = Counter(
        str(row.get("llm_gate_reason") or "") for row in result_rows
    )
    print("llm_gate_reason_distribution:")
    for gate_reason in sorted(reason_counts):
        print(f"  {gate_reason}: {reason_counts[gate_reason]}")


def run(argv: Sequence[str] | None = None) -> int:
    """Run dataset-based llm-gating evaluation and emit JSONL results.

    Inputs:
    - argv: Optional CLI args; defaults to `sys.argv[1:]` when None.

    Outputs:
    - Process-style exit code (`0` success, non-zero on failure).

    Edge cases:
    - Missing dataset file returns non-zero with explicit error text.
    - Deterministic mode works without private overlay by installing local env
      defaults and deterministic retrieval/LLM stubs.

    Concurrency/atomicity:
    - Sequential row execution and file write.
    """

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.max_rows is not None and args.max_rows <= 0:
        print("--max-rows must be greater than zero when provided.", file=sys.stderr)
        return 1
    if args.top_k is not None and args.top_k <= 0:
        print("--top-k must be greater than zero when provided.", file=sys.stderr)
        return 1

    try:
        dataset_path = _resolve_dataset_path(args.dataset)
    except Exception as error:
        print(f"Failed to resolve dataset path: {error}", file=sys.stderr)
        return 1
    try:
        output_path = _resolve_output_path(args.out)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    dataset_files = _resolve_input_dataset_files(dataset_path)
    if not dataset_files:
        if dataset_path.is_dir():
            dataset_files = [DEFAULT_DATASET_PATH]
        else:
            print(
                f"Dataset path not found: {dataset_path}. "
                "Set --dataset to a valid JSONL file or directory.",
                file=sys.stderr,
            )
            return 1

    try:
        runtime = _initialize_runtime(args.mode)
    except Exception as error:
        if args.mode == MODE_DETERMINISTIC:
            print(
                "Failed to initialize deterministic mode. "
                "Run with --mode vertex if you need real backend integrations, "
                "and configure DATASET_POINTER_PATH or CHUNKS_PATH/BUCKET_NAME.",
                file=sys.stderr,
            )
        elif args.mode == MODE_VERTEX:
            print(
                "Failed to initialize vertex mode. Ensure backend environment is set "
                "and dataset cache pointers are valid (DATASET_POINTER_PATH or CHUNKS_PATH).",
                file=sys.stderr,
            )
        else:
            print(
                "Failed to initialize integrated_retrieval_only mode. Ensure backend "
                "environment is set and integrated retrieval dependencies are configured.",
                file=sys.stderr,
            )
        print(f"Initialization error: {error}", file=sys.stderr)
        return 1

    overrides = EvalCliOverrides(
        weighted_score_threshold=args.weighted_score_threshold,
        bm25_score_threshold=args.bm25_score_threshold,
        top_k=args.top_k,
    )
    effective_settings = _resolve_effective_eval_settings(
        runtime,
        weighted_score_threshold_override=overrides.weighted_score_threshold,
        bm25_score_threshold_override=overrides.bm25_score_threshold,
        top_k_override=overrides.top_k,
    )
    output_rows: list[dict[str, Any]] = [
        _build_run_metadata_row(
            runtime,
            dataset_argument=args.dataset,
            dataset_path=dataset_path,
            dataset_files=dataset_files,
            output_path=output_path,
            max_rows=args.max_rows,
            effective_settings=effective_settings,
        )
    ]
    for dataset_file in dataset_files:
        try:
            dataset_rows = _load_dataset_rows(dataset_file, max_rows=args.max_rows)
        except Exception as error:
            print(f"Failed to load dataset {dataset_file}: {error}", file=sys.stderr)
            return 1

        output_rows.append(
            _build_dataset_metadata_row(
                dataset_file,
                row_count=len(dataset_rows),
            )
        )
        result_rows: list[dict[str, Any]] = []
        for dataset_row in dataset_rows:
            try:
                result_row = _build_result_row(
                    runtime,
                    dataset_row,
                    effective_settings=effective_settings,
                )
            except Exception as error:
                print(
                    f"Failed evaluating dataset row id={dataset_row.id}: {error}",
                    file=sys.stderr,
                )
                return 1
            result_rows.append(result_row)
        output_rows.extend(result_rows)

        print(f"dataset={dataset_file}")
        _print_summary(result_rows)

    try:
        _write_results_jsonl(output_path, output_rows)
    except Exception as error:
        print(
            f"Failed writing output JSONL {output_path}: {error}",
            file=sys.stderr,
        )
        return 1

    print(f"wrote_results={output_path}")

    return 0


def main() -> int:
    """CLI entry point wrapper for `python backend/scripts/eval_gating.py`.

    Inputs:
    - None. Reads CLI args from process argv.

    Outputs:
    - Process-style exit code from `run`.

    Edge cases:
    - Mirrors `run` behavior exactly.

    Concurrency/atomicity:
    - Delegates to `run`; no additional side effects.
    """

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
