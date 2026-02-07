"""Replay one or more JSONL datasets through RAG and record gating metrics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
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
DEFAULT_SEARCH_TOP_K = 8
DEFAULT_EMBEDDING_MODEL_NAME = "text-embedding-004"
EVAL_ENABLE_THINKING_GATING = True
EVAL_ENABLE_LLM_CALL_GATING = True
ANSWER_HEAD_CHAR_LIMIT = 120
MODE_DETERMINISTIC = "deterministic"
MODE_VERTEX = "vertex"
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
    - mode: Execution mode (`deterministic` or `vertex`).
    - retrieval_module: Configured retrieval module with embedding/vector/chunks.
    - llm_backend: LLM backend implementation selected for this run.
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
    llm_backend: Any
    settings: Any
    rag_chat_orchestrator: Any


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
      signal-gating, so no gate-enable CLI flags are exposed.

    Concurrency/atomicity:
    - Pure parser construction with no shared-state mutation.
    """

    parser = argparse.ArgumentParser(
        description="Replay dataset questions through RAG and collect signal-gating metrics."
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
            "Output directory for generated JSONL results. "
            "Filename is auto-generated as gating_eval_output_YYYY-MM-DD_HH-MM-SS.jsonl. "
            "Default: ./out"
        ),
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        help="Optional limit on number of rows to evaluate from the dataset.",
    )
    parser.add_argument(
        "--mode",
        choices=[MODE_DETERMINISTIC, MODE_VERTEX],
        default=MODE_DETERMINISTIC,
        help="Runtime wiring mode. Default: deterministic (offline, no Vertex calls).",
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


def _resolve_output_directory(output_argument: str) -> Path:
    """Resolve and validate output directory from CLI input.

    Inputs:
    - output_argument: Raw `--out` CLI value.

    Outputs:
    - Directory path where timestamped output file will be created.

    Edge cases:
    - Existing file paths are rejected.
    - Values ending in `.jsonl` are rejected because `--out` is directory-only.

    Concurrency/atomicity:
    - Pure path validation without filesystem mutations.
    """

    output_directory = Path(output_argument).expanduser()
    if output_directory.suffix.lower() == DEFAULT_OUTPUT_FILENAME_EXTENSION:
        raise ValueError(
            "--out must be a directory path, not a .jsonl filename."
        )
    if output_directory.exists() and output_directory.is_file():
        raise ValueError("--out must be a directory path, but a file was provided.")
    return output_directory


def _build_output_path(output_directory: Path) -> Path:
    """Build timestamped output file path inside the configured output directory.

    Inputs:
    - output_directory: Directory path returned by `_resolve_output_directory`.

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


def _initialize_runtime(mode: str) -> EvaluationRuntime:
    """Initialize orchestrator runtime dependencies for the selected mode.

    Inputs:
    - mode: Execution mode (`deterministic` or `vertex`).

    Outputs:
    - `EvaluationRuntime` with configured retrieval/LLM dependencies.

    Edge cases:
    - Deterministic mode configures in-memory deterministic clients/chunks.
    - Vertex mode configures embedding backend and dataset cache wiring.

    Concurrency/atomicity:
    - Configures module-level retrieval clients/chunks once for the run.
    """

    if mode == MODE_DETERMINISTIC:
        _apply_deterministic_required_env_defaults()

    from api import llm_backends, rag_chat_orchestrator, retrieval
    from api.settings import settings

    if mode == MODE_DETERMINISTIC:
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
        from api import dataset_cache

        embedding_model_name = (
            os.getenv("EMBEDDING_MODEL")
            or os.getenv("DATAPOINTS_MODEL")
            or DEFAULT_EMBEDDING_MODEL_NAME
        )
        retrieval.configure_vertex_embedding_client(
            project=settings.PROJECT_ID,
            region=settings.REGION,
            model_name=embedding_model_name,
        )
        retrieval.configure_vector_client(None)
        cache = dataset_cache.reload_cache()
        retrieval.configure_chunk_store(cache.chunks_by_id)
        llm_backend = llm_backends.get_llm_backend(default_backend=MODE_VERTEX)
        return EvaluationRuntime(
            mode=mode,
            retrieval_module=retrieval,
            llm_backend=llm_backend,
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


def _build_result_row(
    runtime: EvaluationRuntime,
    dataset_row: DatasetQuestionRow,
    dataset_file: Path,
    *,
    weighted_score_threshold_override: float | None,
    bm25_score_threshold_override: float | None,
) -> dict[str, Any]:
    """Execute one question through orchestrator and build JSON-safe result row.

    Inputs:
    - runtime: Preconfigured evaluation runtime dependencies.
    - dataset_row: Question row to evaluate.
    - dataset_file: Source dataset file for this row.
    - weighted_score_threshold_override: Optional weighted score threshold override.
    - bm25_score_threshold_override: Optional BM25 threshold override.

    Outputs:
    - Dict suitable for JSONL output with gating metrics and privacy-safe fields.

    Edge cases:
    - When the question is skipped by signal gate, citations and usage detail may
      be empty/None by design.
    - Full answer text and chunk text are intentionally omitted from output.

    Concurrency/atomicity:
    - Orchestrator call is stateless; function has no external side effects.
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

    start_time = time.perf_counter()
    chat_result = runtime.rag_chat_orchestrator.run_rag_chat(
        dataset_row.question,
        retrieval=runtime.retrieval_module,
        llm_backend=runtime.llm_backend,
        top_k=DEFAULT_SEARCH_TOP_K,
        persona_name=runtime.settings.PERSONA_NAME,
        max_input_tokens=runtime.settings.MAX_INPUT_TOKENS,
        max_output_tokens=runtime.settings.MAX_OUTPUT_TOKENS,
        enable_thinking_gating=EVAL_ENABLE_THINKING_GATING,
        default_thinking_budget_tokens=runtime.settings.THINKING_BUDGET_TOKENS,
        enable_llm_call_gating=EVAL_ENABLE_LLM_CALL_GATING,
        weighted_score_threshold=weighted_score_threshold_effective,
        bm25_score_threshold=bm25_score_threshold_effective,
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
        "dataset_file": str(dataset_file),
        "id": dataset_row.id,
        "question": dataset_row.question,
        "mode": runtime.mode,
        "elapsed_ms": elapsed_ms,
        "signal_would_skip_llm": chat_result.signal_would_skip_llm,
        "signal_gate_reason": chat_result.signal_gate_reason,
        "top1_weighted_score": chat_result.top1_weighted_score,
        "top1_bm25_score": chat_result.top1_bm25_score,
        "top1_vector_score": chat_result.top1_vector_score,
        "weighted_score_threshold": chat_result.weighted_score_threshold,
        "bm25_score_threshold": chat_result.bm25_score_threshold,
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
            and bool(row.get("signal_would_skip_llm"))
        )
        false_calls = sum(
            1
            for row in rows_with_supported_expected
            if row.get("expected") == EXPECTED_LABEL_SKIP
            and not bool(row.get("signal_would_skip_llm"))
        )

        print(
            "false_skips="
            + _format_rate(false_skips, expected_call_or_borderline_count)
        )
        print("false_calls=" + _format_rate(false_calls, expected_skip_count))

    reason_counts = Counter(
        str(row.get("signal_gate_reason") or "") for row in result_rows
    )
    print("signal_gate_reason_distribution:")
    for signal_reason in sorted(reason_counts):
        print(f"  {signal_reason}: {reason_counts[signal_reason]}")


def run(argv: Sequence[str] | None = None) -> int:
    """Run dataset-based signal-gating evaluation and emit JSONL results.

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

    try:
        dataset_path = _resolve_dataset_path(args.dataset)
    except Exception as error:
        print(f"Failed to resolve dataset path: {error}", file=sys.stderr)
        return 1
    try:
        output_directory = _resolve_output_directory(args.out)
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

    output_path = _build_output_path(output_directory)

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
        else:
            print(
                "Failed to initialize vertex mode. Ensure backend environment is set "
                "and dataset cache pointers are valid (DATASET_POINTER_PATH or CHUNKS_PATH).",
                file=sys.stderr,
            )
        print(f"Initialization error: {error}", file=sys.stderr)
        return 1

    all_result_rows: list[dict[str, Any]] = []
    for dataset_file in dataset_files:
        try:
            dataset_rows = _load_dataset_rows(dataset_file, max_rows=args.max_rows)
        except Exception as error:
            print(f"Failed to load dataset {dataset_file}: {error}", file=sys.stderr)
            return 1

        result_rows: list[dict[str, Any]] = []
        for dataset_row in dataset_rows:
            try:
                result_row = _build_result_row(
                    runtime,
                    dataset_row,
                    dataset_file,
                    weighted_score_threshold_override=args.weighted_score_threshold,
                    bm25_score_threshold_override=args.bm25_score_threshold,
                )
            except Exception as error:
                print(
                    f"Failed evaluating dataset row id={dataset_row.id}: {error}",
                    file=sys.stderr,
                )
                return 1
            result_rows.append(result_row)
        all_result_rows.extend(result_rows)

        print(f"dataset={dataset_file}")
        _print_summary(result_rows)

    try:
        _write_results_jsonl(output_path, all_result_rows)
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
