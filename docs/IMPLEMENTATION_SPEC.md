# IMPLEMENTATION_SPEC

## Repos
- Public mono-repo: Contains both backend and frontend. Backend runtime code is in `backend/api/`, ingestion jobs in `backend/jobs/`, and operational scripts in `backend/scripts/`. The frontend app is in `frontend/web/`. A private folder for secrets may be referenced during runtime and is not committed.

## Terminology
See [GLOSSARY.md](./GLOSSARY.md) for definitions of RAG, embeddings, tokens, and other retrieval terms used throughout this spec.

## Environment variables
Backend configuration is loaded from process environment. When `PRIVATE_DIR` is set, `settings.py` also loads `${PRIVATE_DIR}/secrets/common.env` and `${PRIVATE_DIR}/secrets/backend.env` (without overriding already-set env vars).

- **PRIVATE_DIR**: Base directory for private configuration.  
  - Defaults to `./private` if not set, or can be overridden via a `.privatedir` file or an environment variable.  
  - The backend expects secrets in `${PRIVATE_DIR}/secrets/backend.env`.  
  - This folder is not committed, but a template is provided under `private-template/`.

- **Shared variables (loaded from `secrets/common.env`):**
  - `PROJECT_ID`: Shared identifier for both Firebase and GCP resources.
  - `SESSION_COOKIE_ENABLED`: Canonical session mode toggle shared by backend and frontend (`true` for cookie sessions, `false` for bearer mode). Frontend derives `NEXT_PUBLIC_USE_COOKIE_SESSION` from this value when the frontend-specific variable is unset.

- **Backend variables (loaded from backend.env):**
  - `PERSONA_NAME`: Display name used in mock responses.
  - `REGION`: GCP region.
  - `VECTOR_BACKEND`: `local` (default) or `matching_engine`.
  - `LLM_BACKEND`: `vertex` (integrated app default) or `deterministic` (mock default).
  - `INDEX_ENDPOINT_ID`: Vertex AI Index Endpoint ID (required only for `VECTOR_BACKEND=matching_engine`).
  - `DEPLOYED_INDEX_ID`: Deployed Index resource ID (required only for `VECTOR_BACKEND=matching_engine`).
  - `BUCKET_NAME`: GCS bucket used for persona artifacts.
  - `DATASET_URI`: Optional dataset root override (`gs://...`, `file:/...`, or local path). If unset, runtime uses `gs://$BUCKET_NAME`.
  - `DATASET_POINTER_PATH`: Optional pointer-path signal used by startup fallback logic.
  - `CHUNKS_PATH`: Legacy chunk object name (fallback path).
  - `OPS_AUTH`: `enabled` (default) or `disabled` for local dev (bypasses ops auth).
  - `OPS_SECRET`: Required when `OPS_AUTH=enabled` for `/ops/*` endpoints.
  - `API_KEY`: Required secret used as JWT signing fallback when `JWT_SECRET` is unset; **not** an access key.
  - `MAX_INPUT_TOKENS`: Input context budget for LLM calls (defaults to 8000 if unset).
  - `MAX_OUTPUT_TOKENS`: Output budget for LLM calls (hard limit enforced in settings; must be <= 4000).
  - `TOP_K`: Retrieval depth used by chat/eval candidate selection (defaults to 4).
  - `THINKING_BUDGET_TOKENS`: Optional cap for Gemini "thinking" tokens.
  - `ENABLE_THINKING_GATING`: Enables deterministic per-request thinking-budget gating.
  - `ENABLE_LLM_CALL_GATING`: Enables deterministic retrieval-signal gate for LLM calls.
  - `WEIGHTED_SCORE_THRESHOLD`: Weighted-score threshold used by LLM call gating (default 0.55).
  - `BM25_SCORE_THRESHOLD`: BM25 threshold used by LLM call gating fallback (default 3.0).
  - `WEIGHTED_CONSENSUS_COUNT`: Minimum number of chunks that must meet `WEIGHTED_SCORE_THRESHOLD` for semantic signal gating to pass (defaults to 2).
  - `RETRIEVAL_VECTOR_WEIGHT`: Hybrid score vector weight (default 0.7).
  - `RETRIEVAL_BM25_WEIGHT`: Hybrid score BM25 weight (default 0.3).
  - `INCLUDE_THOUGHTS`: `false` (default) or `true` to return thought parts.
  - `REQ_TIMEOUT_MS`: Request timeout in milliseconds.
    - Applied to outbound calls that accept timeouts (GCS chunk download, Matching Engine queries, Gemini generation). Some SDK calls may ignore this if they lack timeout support.

`OPS_SECRET` should not be committed; set it via the private overlay, `gcloud run services update --set-env-vars OPS_SECRET=...`, or Secret Manager.

- **Access keys (Firestore):**
  - Stored in collection `access_keys` with fields: `key_hash` (bcrypt), `key_fingerprint` (SHA-256), `expires_at`, `revoked`, optional `label`, `created_at`, `created_by`.
  - Field meanings:
    - `key_hash`: bcrypt hash of the plaintext access key (plaintext is never stored).
    - `key_fingerprint`: SHA-256 fingerprint used for lookup without storing the key.
    - `expires_at`: absolute expiration time for the key (UTC).
    - `revoked`: explicit kill switch; true rejects the key even if unexpired.
    - `label`: optional human-readable label for admin tracking.
    - `created_at`: time the key was created (UTC).
    - `created_by`: identifier for who/what created the key.
  - Create a key via `python backend/scripts/create_access_key.py create --label demo --expires-in 7d`. Plaintext keys are only printed once by the script.

- **Frontend variables (in `frontend/web/.env.local`):**
  - `NEXT_PUBLIC_API_URL`: URL of the backend (e.g. `http://localhost:8080` during local dev).
  - `NEXT_PUBLIC_USE_COOKIE_SESSION`: Optional frontend override for browser session mode. When unset, frontend tooling derives this from shared `SESSION_COOKIE_ENABLED`.

`settings.py` uses `python-dotenv` to load `${PRIVATE_DIR}/secrets/common.env` followed by `${PRIVATE_DIR}/secrets/backend.env` into the process environment before FastAPI starts. Missing required values will raise validation errors on startup.

## Container registry choice
- Use Artifact Registry in the same region as Cloud Run to keep image pulls on Google’s network (no intra-GCP egress) and avoid Docker Hub rate/availability issues.
- IAM stays in GCP (no Docker Hub tokens), with audit logs and org policies applied uniformly across projects and environments.
- One registry works for Cloud Build, CI, Cloud Run, and GKE; tagging per environment fits the same workflow.
- Cost for the current `persona-backend:local` image (~0.212 GB) is \$0/month because the first 0.5 GB is free; even 1 GB of images is only about $0.10/month.


## Backend API
### Endpoints
**Public**
- `GET /health` (liveness) – returns `{ "status": "ok" }`.
- `GET /ready` (readiness) – returns `{ "ready": true }` when startup completed (local readiness only), otherwise 503.
- `POST /auth/key-login` – accepts `{ "key": "<access key>" }`, returns token metadata, and sets an HttpOnly session cookie when cookie sessions are enabled.
- `POST /auth/logout` – returns 204 and clears the session cookie when enabled.

**Protected**
- `POST /chat` – accepts JSON and returns structured JSON. Integrated mode returns 503 when the chunk store is not loaded at startup or downstream services are unavailable.
- `GET /ops/vector/status` – returns loaded version + pointer version (requires `x-ops-secret` when ops auth is enabled).
- `POST /ops/vector/reload` – reloads the dataset cache (rate-limited to 1 per 10s).

### Request schema
- `question`: str

### Response schema
- `answer`: str
- `citations`: List[Citation]
- `usage`: Usage
- `input_token_limit`: Optional[int] (echoes the configured MAX_INPUT_TOKENS)
- `model`: Optional[str] (resolved model name)

`usage` fields:
- `input_tokens`: int
- `output_tokens`: int
- `thoughts_tokens`: Optional[int]

### Auth
- `/chat` requires authentication and accepts either:
  - a session cookie (default browser mode), or
  - `Authorization: Bearer <token>` (supported fallback mode).
- Cookie mode defaults:
  - Backend default: `SESSION_COOKIE_ENABLED=true`.
  - Frontend default: derives from shared `SESSION_COOKIE_ENABLED` when `NEXT_PUBLIC_USE_COOKIE_SESSION` is unset.
- Session refresh policy:
  - Sliding refresh near expiry is enabled in `/chat`.
  - `REFRESH_WINDOW_SECONDS=300` (5 minutes): when a valid session is within this window, the backend issues refreshed session credentials.
  - In cookie mode, refresh is applied by resetting the HttpOnly session cookie.
  - In bearer mode, refresh is returned via response headers (`x-session-token`, `x-session-expires-at`).
- Key login response includes `model` and `input_token_limit` for the active session.

### Minimal examples
See [backend/README.md#curl-examples](../backend/README.md#curl-examples) for runnable curl commands and current local ports.

## Retrieval and LLM pipeline
- `api/retrieval.py`: first-person normalization and retrieval pipeline (`embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt`).
- `api/dataset_cache.py`: versioned dataset loader + in-process cache with pointer-based reloads.
- `api/vector_backends.py`: local cosine search (default) or Matching Engine (optional).
- `api/rag_chat_orchestrator.py`: shared RAG flow for main and mock handlers.
- `api/llm.py`: prompt construction and Gemini Flash calls.
- `api/llm_backends.py`: Vertex LLM vs deterministic mock backend selection.
- `api/ops_routes.py` + `api/ops_security.py`: `/ops/vector/status` and `/ops/vector/reload` with header auth + rate limiting.

### Retrieval internals (current behavior)
- Query embedding path: normalize question -> `embed_query(...)` -> L2-normalized vector search (`search_vector_store(...)`).
- Hybrid scoring (`apply_filters_and_boosting(...)`):
  - `vector_score = 1 / (1 + distance)`
  - `bm25_norm = bm25 / (bm25 + 1)` for positive BM25 scores
  - `weighted_score = RETRIEVAL_VECTOR_WEIGHT * vector_score + RETRIEVAL_BM25_WEIGHT * bm25_norm`
  - Plus fixed metadata boosts (`profile` match and topic overlap).
- BM25 tokenization/indexing:
  - token pattern: lowercase alphanumeric (`[a-z0-9]+`)
  - token filter: remove tokens shorter than 3 chars and stopword/template terms
  - indexed fields per chunk: `text`, `metadata.section`, `metadata.topics[]`, `metadata.tags[]`
  - BM25 index is built in memory whenever chunk store is configured or reloaded.

### Deterministic duration routing
- Runs in `api/rag_chat_orchestrator.py` after non-English/greeting guards and before retrieval.
- Trigger requirements:
  - `is_duration_intent(question)` is true.
  - Duration families are resolved from `backend/config/experience_domain_config.json`.
- Data source:
  - Uses `retrieval.get_chunk_store_snapshot()` and computes years from metadata intervals, not from top-k retrieval results.
  - Only `section == "Experience"` metadata is included.
- Behavior:
  - Returns a deterministic answer and bypasses LLM calls.
  - Supports combined-domain questions with a union total and per-family breakdown.
  - Emits `llm_gate_reason=duration_bypass`.
  - Returns empty citations (same deterministic-bypass convention as other pre-LLM routes).

### Thinking gate methodology (deterministic)
- Scope:
  - Thinking gating only affects the LLM `thinking_budget_tokens` value passed to the backend.
  - It does **not** decide whether the LLM is called; that is handled by the LLM call gate.
- Feature flag and defaults:
  - `THINKING_BUDGET_TOKENS` is the default budget from environment.
  - `ENABLE_THINKING_GATING` controls whether per-request heuristic gating is applied.
- Resolution flow (`_resolve_thinking_budget_tokens(...)` in `api/rag_chat_orchestrator.py`):
  1. If `THINKING_BUDGET_TOKENS` is unset, effective budget is `None` (provider default behavior).
  2. If thinking gating is disabled, effective budget is exactly `THINKING_BUDGET_TOKENS`.
  3. If thinking gating is enabled, `choose_thinking_budget_tokens(...)` is used:
     - Returns `0` for "simple" questions.
     - Returns the configured default budget for non-simple questions.
- Simple-question heuristic (`_is_simple_question(...)`):
  - Empty or whitespace-only question => simple (`true`).
  - Question length `>= 120` chars => not simple (`false`).
  - More than 1 question mark => not simple (`false`).
  - Case-insensitive prefix match on:
    - `do you have experience`
    - `what is`
    - `define`
    - `list`
    - `summarize`
    => simple (`true`).
  - Otherwise, if question contains any of these reasoning keywords:
    - `compare`, `tradeoff`, `design`, `debug`, `why`, `how`, `step`,
      `recommend`, `pros`, `cons`, `architecture`, `root cause`
    => not simple (`false`).
  - If none of the above blocks match => simple (`true`).
- Important implementation note:
  - `selected_chunks_count` is currently passed into `choose_thinking_budget_tokens(...)` but not used in the heuristic (reserved for future policy changes).

### LLM call gate methodology (deterministic)
- Scope:
  - This gate controls whether the orchestrator calls the LLM after retrieval.
  - The gate computes a deterministic shadow decision for logs/telemetry in all cases.
- Inputs and normalization (`compute_llm_gate_decision(...)` and `_compute_llm_gate_decision(...)`):
  - Inputs: `selected_chunks`, `WEIGHTED_SCORE_THRESHOLD`, `BM25_SCORE_THRESHOLD`, `WEIGHTED_CONSENSUS_COUNT`, and optional `question_is_in_domain`.
  - `WEIGHTED_CONSENSUS_COUNT` is normalized to at least `1`.
  - If `question_is_in_domain` is omitted, it defaults to `bool(selected_chunks)`.
- Score extraction:
  - `top1_*` scores come from the first selected chunk.
  - `best_weighted_score` and `best_bm25_score` are max values across selected chunks (ignoring missing/non-numeric values).
  - `weighted_consensus_count` is the number of selected chunks with `score >= WEIGHTED_SCORE_THRESHOLD`.
- Gate pass logic:
  - `passes_semantic_signal` is true when:
    - `best_weighted_score >= WEIGHTED_SCORE_THRESHOLD`, and
    - `weighted_consensus_count >= WEIGHTED_CONSENSUS_COUNT`.
  - `passes_bm25_fallback_signal` is true when:
    - `best_bm25_score >= BM25_SCORE_THRESHOLD`, and
    - `question_is_in_domain == true`.
  - `would_call_llm = passes_semantic_signal OR passes_bm25_fallback_signal`.
- Decision reasons emitted:
  - `no_candidates`: no selected chunks.
  - `pass`: gate passes by semantic signal and/or BM25 fallback.
  - `score_below`: selected chunks exist but pass conditions are not met.
- Runtime behavior in `/chat`:
  - If `ENABLE_LLM_CALL_GATING=false`, shadow decision is still computed and logged, but LLM invocation proceeds whenever retrieval selected at least one chunk.
  - If `ENABLE_LLM_CALL_GATING=true`, LLM invocation follows `would_call_llm`.
  - When gating is enabled and gate fails, `/chat` returns the deterministic no-signal answer with empty citations (no LLM generation call).

## Ingestion jobs
- `jobs/pack_and_push.py` validates JSONL against `schema/chunk.schema.json`, and if any bullet **or paragraph** exceeds ~2.2k characters (~450 tokens), splits it by sentence boundaries (never mid-sentence). It can also write `chunks.jsonl.gz` into a dataset folder via `--output-dir`.
- `jobs/build_datapoints.py` calls the configured Vertex AI embedding model (default `text-embedding-004` at 768 dims), writes **unit-normalized** embeddings to `datapoints.jsonl`, and emits a required `manifest.json` (embedding model, dimensions, and count). Embedding calls are batched in groups of 16 (`DATAPOINTS_BATCH_SIZE`) and `DATAPOINTS_DIMENSIONS` is enforced to keep vectors consistent with your index and manifest.

`manifest.json` required fields:
- `version`
- `created_at`
- `datapoints_file`
- `chunks_file`
- `embedding_model`
- `dimensions`
- `num_datapoints`

Validation is performed against the machine-readable schema:
- [`chunk.schema.json`](../backend/schema/chunk.schema.json)

For a human-readable guide explaining the meaning, use cases, benefits, and trade-offs of each field, see:
- [`SCHEMA.md`](SCHEMA.md)

## Ingestion and Retrieval Design

### Overall scheme

- **One profile per CV**: each chunk keeps a single `profile` string for restricts/boosts.
- **Tags**:
  - Always add `profile:<profile>`.
  - Optionally add `topic:*` for precision (`topic:kubernetes`, `topic:roadmap`).
- **Chunks**: store as JSONL lines with text + metadata; keep gzipped in GCS as `datasets/<version>/chunks.jsonl.gz` and switch versions via `datasets/current.json`.
- **Vectors**: embeddings from each chunk → local cosine search by default; optional upsert to **Vertex AI Matching Engine** for semantic search.
- **BM25**: lightweight inverted index over the same chunks; built in memory at startup.

### Ingestion stage (one-time or occasional)

1. **Chunking**
   - Split CV docs into ~450-token chunks (could be generated via an LLM for the initial corpus; keep source material private).
   - Attach `profile` and tags (`profile:<profile>` + optional `topic:*`).
   - Validate against `chunk.schema.json`.
2. **Packaging**
   - Write `chunks.jsonl.gz` into `datasets/<version>/`.
   - Upload the dataset folder to GCS.
3. **Embedding + upsert**
   - `backend/jobs/build_datapoints.py` batches persona chunks, calls the selected Vertex embedding model, writes `datapoints.jsonl`, and emits `manifest.json` with model/dim/count metadata.
   - `make gcp-index-upsert` remains available if you choose `VECTOR_BACKEND=matching_engine`.

> Next implementation stage: deepen verification for the now-live runtime, add golden tests for retrieval + LLM prompts, cover API key/rate-limit branches, and run the end-to-end integration test against the integrated backend.

### Vector search configuration (Vertex AI Matching Engine)
This section applies only when `VECTOR_BACKEND=matching_engine`.
- **Index type:** Tree-AH with dot-product distance; match cosine behaviour by L2-normalizing every embedding (during upsert and query).
- **Dimensions:** Match `DATAPOINTS_DIMENSIONS`/`DATAPOINTS_MODEL` (3,072 with `gemini-embedding-001`, 768 with the `text-embedding-00x` family).
- **Replicas:** `ME_MIN_REPLICAS`/`ME_MAX_REPLICAS` control the min/max replica counts during `make gcp-index-deploy` (default 1/1); bump the max to allow autoscaling.
- **Tuning parameters:** `approximateNeighborsCount=100`, `leafNodeEmbeddingCount=1000`, and `leafNodesToSearchPercent=7`. Raise the neighbor count or search percent for higher recall (at the cost of latency), or lower them for faster, less exhaustive searches.
- **Provisioning:** Root Makefile targets (`make gcp-index-create`, `make gcp-index-endpoint-create`, `make gcp-index-deploy`, `make gcp-index-upsert`) generate the JSON metadata and call `gcloud` using env vars from `private/secrets`. Use `make gcp-index-list` to inspect existing indexes.

### Deployment/runtime stage (every query)

1. **Startup (Cloud Run container)**
   - Load env vars (`BUCKET_NAME`, `VECTOR_BACKEND`, etc.).
   - Resolve `datasets/current.json` to a version folder and load `datapoints.jsonl`, `chunks.jsonl.gz`, `manifest.json`.
   - Validate manifest compatibility before serving traffic: `embedding_model` must match the runtime embedding model, and `dimensions` must match `DATAPOINTS_DIMENSIONS` when set; mismatches fail startup/reload early.
   - Build in-memory BM25 inverted index (tokenize chunks, compute idf, etc.).
2. **Query flow**
   - **First-person normalization** (convert “Omer Reznik” → “I”).
   - **Profile classification**: keyword-hint heuristic can bias `infra`/`product`; other profiles remain neutral.
   - **Vector search**: embed query, run local cosine search by default or call Vertex Matching Engine when configured.
   - **BM25 scoring**: run query tokens against the in-memory BM25 index over chunk text/section/topics/tags.
   - **Rerank/boost**:
     - Weight scores with env-configured vector/BM25 weights and fixed profile/topic boosts.
     - If classified, boost chunks with matching profile tag.
   - **Trim**: keep at most 8 chunks after reranking (candidate depth comes from `TOP_K`, default 4).
   - **Prompt LLM**: feed selected reranked chunks into Gemini Flash, generate strict, grounded first-person answer.
   - **Return**: `{answer, citations, usage}` JSON.
See [`DATA_DESIGN_RATIONALE.md`](./DATA_DESIGN_RATIONALE.md) for discussion.

## Security details
- `/auth/key-login` rate limits before bcrypt: 10 attempts per 10 minutes per IP and 5 per fingerprint (in-memory today).
- `/chat` requires auth and rate limits per IP (no per-access-key limiting on `/chat` yet).

## Frontend behavior
- Frontend present under `frontend/web/`.

## Tests
- Python tests under `tests/`:
  - `backend/tests/conftest.py`
  - `backend/tests/test_integration_real_backend.py`
  - `backend/tests/test_normalize_question_punct.py`
  - `backend/tests/test_persona_voice.py`
  - `backend/tests/test_smoke.py`

## Compatibility and tooling
- Python 3.13 compatible, avoid optional wheels unless verified. `orjson` is optional.
