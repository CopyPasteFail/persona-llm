"""Trace BM25 tokenization, postings statistics, and per-term score contributions.

How to run:
- Compile check:
  python3 -m py_compile backend/scripts/debug_bm25_trace.py

- Integrated retrieval (real dataset via backend wiring):
  python3 backend/scripts/debug_bm25_trace.py \
    --mode integrated_retrieval_only \
    --private-dir /path/to/private \
    --backend-env /path/to/private/secrets/backend.env \
    --query "Do you have experience in dentistry?" \
    --chunk-id <chunk_id_1> \
    --chunk-id <chunk_id_2>

- Deterministic sanity mode (toy corpus, no PRIVATE_DIR required):
  python3 backend/scripts/debug_bm25_trace.py \
    --mode deterministic \
    --query "Do you have experience in dentistry?" \
    --chunk-id product-001 \
    --chunk-id infra-001
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

# Ensure local imports resolve when run as a script from repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Settings are imported by api.retrieval; provide import-safe defaults at file top.
# Real values from shell env or --backend-env override these placeholders.
_IMPORT_ENV_DEFAULTS: dict[str, str] = {
    "PERSONA_NAME": "Debug Persona",
    "PROJECT_ID": "debug-project",
    "REGION": "us-central1",
    "LLM_BACKEND": "deterministic",
    "API_KEY": "debug-api-key",
    "MAX_OUTPUT_TOKENS": "256",
    "REQ_TIMEOUT_MS": "10000",
    "BUCKET_NAME": "debug-bucket",
}
for _env_name, _env_value in _IMPORT_ENV_DEFAULTS.items():
    os.environ.setdefault(_env_name, _env_value)

MODE_INTEGRATED_RETRIEVAL_ONLY = "integrated_retrieval_only"
MODE_DETERMINISTIC = "deterministic"
SUPPORTED_MODES = (MODE_INTEGRATED_RETRIEVAL_ONLY, MODE_DETERMINISTIC)

TOY_CHUNKS: dict[str, dict[str, Any]] = {
    "product-001": {
        "chunk_id": "product-001",
        "text": "I led product roadmap planning and launch coordination for checkout.",
        "section": "product",
        "doc_id": "do-you-have-experience-in-dentistry-product-001",
        "source_uri": "https://example.com/do-you-have-experience-in-dentistry",
        "topics": ["roadmap", "experiments"],
        "tags": ["topic:product", "topic:checkout"],
        "extras": {"keywords": ["do", "you", "have", "experience", "dentistry"]},
    },
    "infra-001": {
        "chunk_id": "infra-001",
        "text": "I run Kubernetes clusters and Terraform delivery pipelines.",
        "section": "infra",
        "doc_id": "infra-001",
        "source_uri": "https://example.com/infra-001",
        "topics": ["kubernetes", "terraform"],
        "tags": ["topic:kubernetes", "topic:terraform"],
        "extras": {"keywords": ["kubernetes", "terraform", "platform"]},
    },
}


@dataclass(frozen=True)
class RuntimeContext:
    """Resolved BM25 tracing dependencies for one script run.

    Inputs:
    - retrieval_module: Imported `api.retrieval` module.
    - bm25_index: BM25 index instance to inspect.
    - chunks_by_id: Chunk mapping used for token extraction and lookups.
    - doc_index_by_id: Derived doc-index map based on BM25 doc-order keys.
    - corpus_name: Label used in output headings.

    Outputs:
    - Immutable context object for trace printers.

    Edge cases:
    - `doc_index_by_id` may omit unknown chunk ids if not in index lengths.
    """

    retrieval_module: Any
    bm25_index: Any
    chunks_by_id: dict[str, dict[str, Any]]
    doc_index_by_id: dict[str, int]
    corpus_name: str


def _parse_args() -> argparse.Namespace:
    """Parse CLI args for BM25 tracing.

    Inputs:
    - None. Uses process argv.

    Outputs:
    - Parsed argument namespace.

    Edge cases:
    - Requires at least one `--chunk-id` and one `--query` value.
    """

    parser = argparse.ArgumentParser(
        description="Trace exact BM25 internals for query/chunk pairs."
    )
    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default=MODE_INTEGRATED_RETRIEVAL_ONLY,
        help="Runtime mode: integrated retrieval wiring or deterministic toy corpus.",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Raw query string to inspect.",
    )
    parser.add_argument(
        "--chunk-id",
        dest="chunk_ids",
        action="append",
        required=True,
        help="Chunk id to trace. Repeat for multiple ids.",
    )
    parser.add_argument(
        "--private-dir",
        default=None,
        help="Optional PRIVATE_DIR path (used in integrated mode).",
    )
    parser.add_argument(
        "--backend-env",
        default=None,
        help="Optional backend.env path to load before imports (override=True).",
    )
    return parser.parse_args()


def _load_backend_env_if_requested(backend_env_path: str | None) -> None:
    """Load an explicit backend env file before backend module imports.

    Inputs:
    - backend_env_path: Optional filesystem path to a dotenv file.

    Outputs:
    - None. Mutates `os.environ` when a path is provided.

    Edge cases:
    - Raises RuntimeError when path does not exist.
    """

    if not backend_env_path:
        return
    env_path = Path(backend_env_path).expanduser().resolve()
    if not env_path.is_file():
        raise RuntimeError(f"--backend-env file not found: {env_path}")
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("python-dotenv is required to use --backend-env") from exc
    load_dotenv(env_path, override=True)


def _ordered_unique(tokens: list[str]) -> list[str]:
    """Return first-seen unique token order.

    Inputs:
    - tokens: Token list preserving query order.

    Outputs:
    - List with duplicates removed while preserving first occurrence order.
    """

    seen_tokens: set[str] = set()
    ordered_tokens: list[str] = []
    for token in tokens:
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        ordered_tokens.append(token)
    return ordered_tokens


def _subtract_multiset_tokens(raw_tokens: list[str], filtered_tokens: list[str]) -> list[str]:
    """Return tokens removed by filtering while preserving original order.

    Inputs:
    - raw_tokens: Original token sequence before filtering.
    - filtered_tokens: Filtered token sequence.

    Outputs:
    - Ordered list of tokens removed by the filter.
    """

    remaining_filtered = Counter(filtered_tokens)
    removed_tokens: list[str] = []
    for raw_token in raw_tokens:
        if remaining_filtered.get(raw_token, 0) > 0:
            remaining_filtered[raw_token] -= 1
            continue
        removed_tokens.append(raw_token)
    return removed_tokens


def _collect_raw_bm25_field_tokens(runtime: RuntimeContext, chunk: Mapping[str, Any]) -> list[str]:
    """Collect raw tokens from BM25-indexed fields before BM25 filtering.

    Inputs:
    - runtime: Runtime context containing retrieval tokenization helpers.
    - chunk: Chunk record to tokenize.

    Outputs:
    - Raw token list from text, section, topics, and tags.
    """

    raw_tokens: list[str] = []
    tokenize = runtime.retrieval_module._tokenize

    text = chunk.get("text")
    if isinstance(text, str):
        raw_tokens.extend(tokenize(text))

    section = chunk.get("section")
    if isinstance(section, str):
        raw_tokens.extend(tokenize(section))

    topics = chunk.get("topics")
    if isinstance(topics, Iterable) and not isinstance(topics, (str, bytes)):
        for topic in topics:
            if isinstance(topic, str):
                raw_tokens.extend(tokenize(topic))

    tags = chunk.get("tags")
    if isinstance(tags, Iterable) and not isinstance(tags, (str, bytes)):
        for tag in tags:
            if isinstance(tag, str):
                raw_tokens.extend(tokenize(tag))

    return raw_tokens


def _build_doc_index_map(bm25_index: Any) -> dict[str, int]:
    """Build doc-index mapping from BM25 internal length key order.

    Inputs:
    - bm25_index: `_Bm25Index` instance.

    Outputs:
    - Mapping of chunk_id -> doc_index.

    Edge cases:
    - Uses insertion order of `_lengths` keys as the derived doc order.
    """

    doc_ids_in_order = list(bm25_index._lengths.keys())
    return {chunk_id: index for index, chunk_id in enumerate(doc_ids_in_order)}


def _initialize_integrated_runtime(args: argparse.Namespace) -> RuntimeContext:
    """Initialize integrated retrieval runtime using app-equivalent wiring.

    Inputs:
    - args: Parsed script args.

    Outputs:
    - Runtime context with live cache-backed BM25 index.

    Edge cases:
    - Raises when cache loading or BM25 initialization fails.

    Concurrency/atomicity:
    - Reuses module-global retrieval state configured by runtime wiring.
    """

    if args.private_dir:
        os.environ["PRIVATE_DIR"] = str(Path(args.private_dir).expanduser().resolve())

    from api import dataset_cache, retrieval, runtime_wiring
    from api.settings import settings

    runtime_wiring.configure_integrated_retrieval_runtime(
        retrieval_module=retrieval,
        project_id=settings.PROJECT_ID,
        region=settings.REGION,
    )

    cache = dataset_cache.get_or_load_cache()
    bm25_index = retrieval._bm25_index  # type: ignore[attr-defined]
    if bm25_index is None:
        raise RuntimeError("Integrated runtime loaded chunks but BM25 index is None.")

    return RuntimeContext(
        retrieval_module=retrieval,
        bm25_index=bm25_index,
        chunks_by_id=dict(cache.chunks_by_id),
        doc_index_by_id=_build_doc_index_map(bm25_index),
        corpus_name=f"integrated:{cache.dataset_version}",
    )


def _initialize_deterministic_runtime() -> RuntimeContext:
    """Initialize deterministic toy-corpus runtime for BM25 sanity checks.

    Inputs:
    - None.

    Outputs:
    - Runtime context with a toy `_Bm25Index` built from local chunks.

    Edge cases:
    - Requires only local source code and no PRIVATE_DIR.
    """

    from api import retrieval

    bm25_index = retrieval._Bm25Index(TOY_CHUNKS)  # type: ignore[attr-defined]
    return RuntimeContext(
        retrieval_module=retrieval,
        bm25_index=bm25_index,
        chunks_by_id=dict(TOY_CHUNKS),
        doc_index_by_id=_build_doc_index_map(bm25_index),
        corpus_name="deterministic:toy",
    )


def _initialize_runtime(args: argparse.Namespace) -> RuntimeContext:
    """Dispatch runtime initialization by selected mode.

    Inputs:
    - args: Parsed script args.

    Outputs:
    - Runtime context for the requested mode.
    """

    if args.mode == MODE_DETERMINISTIC:
        return _initialize_deterministic_runtime()
    return _initialize_integrated_runtime(args)


def _print_query_tokens(runtime: RuntimeContext, query: str) -> tuple[list[str], Counter[str]]:
    """Print exact BM25 query tokens and counts.

    Inputs:
    - runtime: Runtime context containing retrieval tokenizer.
    - query: Raw query string.

    Outputs:
    - Tuple `(query_tokens, query_token_counts)`.

    Edge cases:
    - Empty queries produce empty token lists and zero scores.
    """

    raw_query_tokens = list(runtime.retrieval_module._tokenize(query))
    bm25_query_tokens = list(runtime.retrieval_module._tokenize_for_bm25(query))
    bm25_query_token_counts = Counter(bm25_query_tokens)
    removed_query_tokens = _subtract_multiset_tokens(raw_query_tokens, bm25_query_tokens)

    print("\n=== QUERY ===")
    print(f"corpus={runtime.corpus_name}")
    print(f"query_raw={query!r}")
    print(f"query_tokens_raw={raw_query_tokens}")
    print(f"query_tokens_bm25={bm25_query_tokens}")
    print(f"query_tokens_removed_by_bm25_filter={removed_query_tokens}")
    print(f"query_token_counts_bm25={dict(sorted(bm25_query_token_counts.items()))}")
    return bm25_query_tokens, bm25_query_token_counts


def _print_chunk_token_summary(
    runtime: RuntimeContext,
    chunk_id: str,
    query_unique_tokens: list[str],
) -> None:
    """Print chunk tokenization summary with query-relevant TF values.

    Inputs:
    - runtime: Runtime context with chunk map and extraction helpers.
    - chunk_id: Chunk id to inspect.
    - query_unique_tokens: Unique query tokens in first-seen order.

    Outputs:
    - None. Writes structured chunk summary output.

    Edge cases:
    - Missing chunk ids are reported explicitly.
    """

    print(f"\n=== CHUNK TOKENS: {chunk_id} ===")
    chunk = runtime.chunks_by_id.get(chunk_id)
    if chunk is None:
        print("chunk_found=false")
        return

    raw_chunk_tokens = _collect_raw_bm25_field_tokens(runtime, chunk)
    chunk_tokens_bm25 = list(runtime.retrieval_module._extract_chunk_tokens(chunk))
    token_frequencies = Counter(chunk_tokens_bm25)
    removed_chunk_tokens = _subtract_multiset_tokens(raw_chunk_tokens, chunk_tokens_bm25)

    doc_index = runtime.doc_index_by_id.get(chunk_id)
    doc_length = runtime.bm25_index._lengths.get(chunk_id, 0)  # type: ignore[attr-defined]
    top_repeated_tokens = sorted(
        token_frequencies.items(), key=lambda item: (-item[1], item[0])
    )[:10]
    query_relevant_term_frequencies = {
        token: token_frequencies.get(token, 0) for token in query_unique_tokens
    }

    print("chunk_found=true")
    print(f"doc_index={doc_index}")
    print(f"doc_len={doc_length}")
    print(f"chunk_tokens_raw={raw_chunk_tokens}")
    print(f"chunk_tokens_bm25={chunk_tokens_bm25}")
    print(f"chunk_tokens_removed_by_bm25_filter={removed_chunk_tokens}")
    print(f"query_relevant_tf={query_relevant_term_frequencies}")
    print(f"top_repeated_tokens={top_repeated_tokens}")
    print(f"chunk_text_preview={str(chunk.get('text', ''))[:180]!r}")


def _compute_idf(*, doc_count: int, document_frequency: int) -> float:
    """Compute IDF with the exact `_Bm25Index.score` formula.

    Inputs:
    - doc_count: Total number of indexed documents.
    - document_frequency: Number of docs containing the term.

    Outputs:
    - IDF float value.
    """

    return math.log(1 + (doc_count - document_frequency + 0.5) / (document_frequency + 0.5))


def _print_score_trace(
    runtime: RuntimeContext,
    chunk_id: str,
    query_tokens: list[str],
    query_token_counts: Counter[str],
) -> None:
    """Print exact BM25 score trace for one query/chunk pair.

    Inputs:
    - runtime: Runtime context with BM25 index internals.
    - chunk_id: Requested chunk id.
    - query_tokens: Full query token list (including duplicates).
    - query_token_counts: Multiplicity per query token.

    Outputs:
    - None. Writes per-term and final score trace output.

    Edge cases:
    - Terms absent from corpus are shown with zero contribution.
    - Missing chunks are reported without scoring.
    """

    print(f"\n=== BM25 TRACE: {chunk_id} ===")
    if chunk_id not in runtime.chunks_by_id:
        print("chunk_found=false")
        return

    bm25_index = runtime.bm25_index
    postings_map = bm25_index._postings  # type: ignore[attr-defined]
    doc_length = bm25_index._lengths.get(chunk_id, 0)  # type: ignore[attr-defined]
    average_length = bm25_index._avg_len  # type: ignore[attr-defined]
    doc_count = bm25_index._doc_count  # type: ignore[attr-defined]
    k1 = bm25_index._k1  # type: ignore[attr-defined]
    b = bm25_index._b  # type: ignore[attr-defined]
    doc_index = runtime.doc_index_by_id.get(chunk_id)

    print(
        "parameters="
        f"{{doc_index:{doc_index}, doc_count:{doc_count}, k1:{k1}, b:{b}, "
        f"doc_len:{doc_length}, avg_len:{average_length}}}"
    )

    unique_query_tokens = _ordered_unique(query_tokens)
    manual_score_sum = 0.0
    for token in unique_query_tokens:
        query_term_count = query_token_counts[token]
        postings = postings_map.get(token, {})
        document_frequency = len(postings)
        term_frequency = postings.get(chunk_id, 0)

        if document_frequency == 0:
            print(
                f"term={token!r} query_tf={query_term_count} tf={term_frequency} "
                "df=0 idf=0.0000000000 numerator=0.0000000000 "
                "denominator=0.0000000000 contribution_per_occurrence=0.0000000000 "
                "contribution_total=0.0000000000 status=not_in_corpus"
            )
            continue

        idf = _compute_idf(doc_count=doc_count, document_frequency=document_frequency)
        numerator = term_frequency * (k1 + 1)
        denominator = term_frequency + k1 * (
            1 - b + b * (doc_length / (average_length or 1.0))
        )
        contribution_per_occurrence = (
            0.0 if term_frequency == 0 else idf * numerator / denominator
        )
        contribution_total = contribution_per_occurrence * query_term_count
        manual_score_sum += contribution_total

        print(
            f"term={token!r} query_tf={query_term_count} tf={term_frequency} "
            f"df={document_frequency} idf={idf:.10f} numerator={numerator:.10f} "
            f"denominator={denominator:.10f} "
            f"contribution_per_occurrence={contribution_per_occurrence:.10f} "
            f"contribution_total={contribution_total:.10f}"
        )

    score_by_chunk_id = bm25_index.score(query_tokens)
    index_score = score_by_chunk_id.get(chunk_id, 0.0)
    delta = abs(manual_score_sum - index_score)

    print(f"manual_score={manual_score_sum:.10f}")
    print(f"index_score={index_score:.10f}")
    print(f"delta={delta:.12f}")


def main() -> None:
    """Run BM25 trace workflow for selected query and chunk ids.

    Inputs:
    - CLI args parsed by `_parse_args`.

    Outputs:
    - Structured stdout report with tokenization and score internals.
    """

    args = _parse_args()
    _load_backend_env_if_requested(args.backend_env)
    runtime = _initialize_runtime(args)

    query_tokens, query_token_counts = _print_query_tokens(runtime, args.query)
    query_unique_tokens = _ordered_unique(query_tokens)

    print("\n=== DOC ORDER ===")
    print("doc_order_source=_lengths_insertion_order")
    print(f"doc_index_by_id={runtime.doc_index_by_id}")

    for chunk_id in args.chunk_ids:
        _print_chunk_token_summary(runtime, chunk_id, query_unique_tokens)

    for chunk_id in args.chunk_ids:
        _print_score_trace(runtime, chunk_id, query_tokens, query_token_counts)


if __name__ == "__main__":
    main()
