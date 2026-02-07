# Retrieval-Only Gating Eval

`backend/scripts/eval_gating.py` supports `--mode integrated_retrieval_only` for running the integrated retrieval path without chat generation.

What this mode does per row:
- Normalizes the question with `normalize_question_for_first_person(...)`.
- Calls `embed_query(...)` using the configured embedding client.
- Calls `search_vector_store(...)` honoring `VECTOR_BACKEND`.
- Calls `apply_filters_and_boosting(...)`.
- Computes gating decision via orchestrator signal logic.
- Never calls chat LLM generation.

Privacy guarantees for this mode:
- No answer text fields are written.
- No chunk text is written.
- Output keeps only IDs and numeric metrics.

## Dataset format

JSONL, one row per line:

```json
{"id":"q001","question":"What platform work did you lead?","expected":"CALL"}
```

Required fields:
- `id`
- `question`

Optional fields:
- `expected`: `CALL`, `SKIP`, or `BORDERLINE`
- `notes`

## Output fields (`integrated_retrieval_only`)

Each row includes:
- `dataset_file`, `id`, `question`, `mode`, `elapsed_ms`
- `would_call_llm_if_gated`, `llm_gate_reason`
- `top1_weighted_score`, `top1_bm25_score`, `top1_vector_score`
- `weighted_score_threshold`, `bm25_score_threshold`
- `selected_chunk_ids`
- `selected_count`, `candidates_count`
- `expected` (when present in dataset)

## Required environment variables

`load_settings()` still validates backend settings, so these are required even though LLM generation is skipped:
- `PERSONA_NAME`
- `PROJECT_ID`
- `REGION`
- `LLM_BACKEND`
- `API_KEY`
- `MAX_OUTPUT_TOKENS`
- `REQ_TIMEOUT_MS`
- `BUCKET_NAME` (required unless `DATASET_URI` is set)

Integrated retrieval config used by this mode:
- `VECTOR_BACKEND=local|matching_engine`
- `DATASET_URI` (optional dataset root override; if unset, `BUCKET_NAME` root is used)
- `EMBEDDING_MODEL` or `DATAPOINTS_MODEL` (optional embedding model override)
- `WEIGHTED_SCORE_THRESHOLD`, `BM25_SCORE_THRESHOLD` (optional defaults)
- `RETRIEVAL_VECTOR_WEIGHT`, `RETRIEVAL_BM25_WEIGHT` (optional hybrid scoring weights)

Matching Engine specific:
- `INDEX_ENDPOINT_ID`
- `DEPLOYED_INDEX_ID`

Vertex/Google auth:
- `GOOGLE_APPLICATION_CREDENTIALS` (or other ADC setup) for embedding calls and Matching Engine calls.

## Run examples

From repo root.

Local vector backend (`VECTOR_BACKEND=local`):

```bash
make be-eval-gating ARGS="--mode integrated_retrieval_only --dataset private/eval_datasets/gating_questions_v1.jsonl --out .out"
```

Matching Engine backend (`VECTOR_BACKEND=matching_engine`):

```bash
VECTOR_BACKEND=matching_engine make be-eval-gating ARGS="--mode integrated_retrieval_only --dataset private/eval_datasets/gating_questions_v1.jsonl --out .out/gating_eval_matching_engine.jsonl"
```

Public sample dataset:

```bash
make be-eval-gating ARGS="--mode integrated_retrieval_only --dataset private-template/eval_datasets/sample_questions.jsonl --out .out"
```

Notes:
- `--dataset` accepts a JSONL file path or a directory containing JSONL files.
- `--out` accepts either a directory (auto timestamped filename) or a `.jsonl` output file path.
- This mode may call Vertex embeddings and (optionally) Matching Engine, so it can consume cloud resources.
