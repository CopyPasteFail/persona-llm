# IMPLEMENTATION_SPEC

## Repos
- Public mono-repo: Contains both backend and frontend. The backend is in `backend/api/` and jobs under `backend/jobs/`. The frontend is in `frontend/web/`. A private folder for secrets may be referenced during runtime, not committed.

## Environment variables
Backend configuration is loaded from a dotenv file rather than a global `PRIVATE_DIR`.

- **PRIVATE_DIR**: Base directory for private configuration.  
  - Defaults to `./private` if not set, or can be overridden via a `.privatedir` file or an environment variable.  
  - The backend expects secrets in `${PRIVATE_DIR}/secrets/backend.env`.  
  - This folder is not committed, but a template is provided under `private-template/`.

- **Shared variables (loaded from `secrets/common.env`):**
  - `PROJECT_ID`: Shared identifier for both Firebase and GCP resources.

- **Backend variables (loaded from backend.env):**
  - `PERSONA_NAME`: Display name used in mock responses.
  - `REGION`: GCP region.
  - `INDEX_ENDPOINT_ID`: Vertex AI Index Endpoint ID (store the trailing ID; the service reconstructs the full resource name).
  - `DEPLOYED_INDEX_ID`: Deployed Index resource ID.
  - `BUCKET_NAME`: GCS bucket used for persona artifacts.
  - `CHUNKS_PATH`: Object name of the packed chunk data.
  - `API_KEY`: Shared secret for JWT signing fallback and any internal calls; **not** an access key.
  - `MAX_INPUT_TOKENS`: Input context budget for LLM calls (defaults to 8000 if unset).
  - `MAX_OUTPUT_TOKENS`: Output budget for LLM calls.
- `REQ_TIMEOUT_MS`: Request timeout in milliseconds.
  - Applied to outbound calls that accept timeouts (GCS chunk download, Matching Engine queries, Gemini generation). Some SDK calls may ignore this if they lack timeout support.

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
  - Create a key via `python backend/scripts/create_access_key.py --label demo --expires-in 7d`. Plaintext keys are only printed once by the script.

- **Frontend variables (in `frontend/web/.env.local`):**
  - `NEXT_PUBLIC_API_URL`: URL of the backend (e.g. `http://localhost:8080` during local dev).

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
- `POST /auth/key-login` – accepts `{ "key": "<access key>" }` and returns a bearer token.
- `POST /auth/logout` – returns 204 and clears the session cookie when enabled.

**Protected**
- `POST /chat` – accepts JSON and returns structured JSON. Real mode returns 503 when the chunk store is not loaded at startup or downstream services are unavailable.

### Request schema
- `question`: str

### Response schema
- `answer`: str
- `citations`: List[Citation]
- `usage`: Usage
- `input_token_limit`: Optional[int] (echoes the configured MAX_INPUT_TOKENS)

### Auth
- `/chat` requires authentication via `Authorization: Bearer <token>`.
- Tokens are issued by `/auth/key-login` and may also be stored in a session cookie when enabled.

### Minimal examples
See [backend/README.md#curl-examples](here) for the runnable curl commands and current local ports.

## Retrieval and LLM pipeline
- `api/retrieval.py`: first-person normalization and retrieval pipeline (`embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt`) implemented; focus now on tests, tuning, and live Vertex integration.
- `api/llm.py`: `build_llm_prompt` returns the strict format, and `call_gemini_flash` is implemented via the Vertex AI Python SDK (Gemini Flash) with basic usage extraction.

## Ingestion jobs
- `jobs/pack_and_push.py` validates JSONL against `schema/chunk.schema.json`, and if any bullet **or paragraph** exceeds ~2.2k characters (~450 tokens), splits it by sentence boundaries (never mid-sentence). Writes `chunks-<sha>.jsonl.gz` with enriched metadata and prints a `gs://` URI if a `bucket:` is provided in a YAML file passed with `--settings`.
- `jobs/build_datapoints.py` loads the same chunk file, calls the configured Vertex AI embedding model (default `gemini-embedding-001` at 3,072 dims), and emits a newline-delimited JSON file ready for `gcloud ai index-endpoints upsert-datapoints`. Embedding calls are batched in groups of 16 (`DATAPOINTS_BATCH_SIZE` in the env) to minimize network round trips without risking payload limits, and the job enforces `DATAPOINTS_DIMENSIONS` so the vectors match the Matching Engine index while staying within the model’s supported dimensionality.

Validation is performed against the machine-readable schema:
- [`chunk.schema.json`](../backend/schema/chunk.schema.json)

For a human-readable guide explaining the meaning, use cases, benefits, and trade-offs of each field, see:
- [`SCHEMA.md`](SCHEMA.md)

## Ingestion and Retrieval Design

### Overall scheme

- **One CV per Role**: one infra (DevOps/SRE/Platform) and one product (PM/TPM/PO).
- **Tags**:
  - Always add `role:infra` or `role:product`.
  - Optionally add `topic:*` for precision (`topic:kubernetes`, `topic:roadmap`).
- **Chunks**: store as JSONL lines with text + metadata; keep gzipped in GCS as the object named by `CHUNKS_PATH` in `BUCKET_NAME`.
- **Vectors**: embeddings from each chunk → upsert to **Vertex AI Matching Engine** for semantic search.
- **BM25**: lightweight inverted index over the same chunks; built in memory at startup.

### Ingestion stage (one-time or occasional)

1. **Chunking**
   - Split CV docs into ~450-token chunks (often generated via ChatGPT for the initial corpus; keep source material private).
   - Attach `role` + optional `topic` tags.
   - Validate against `chunk.schema.json`.
2. **Packaging**
   - Write `chunks-<sha>.jsonl.gz` with metadata fragments.
   - Upload to GCS (sidecar store).
3. **Embedding + upsert**
   - `backend/jobs/build_datapoints.py` (invoked via `make be-build_datapoints`) batches persona chunks, calls the selected Vertex embedding model (default `gemini-embedding-001`), and writes the `DATAPOINTS_FILE` artifact with ready-to-upload datapoints.
   - `make gcp-index-upsert` converts that artifact if needed, uploads it to `gs://$BUCKET_NAME/matching-engine/<timestamp>/datapoints.json`, and triggers `gcloud ai indexes update` so Matching Engine serves the embeddings consumed by `embed_query` → `search_vector_store`.

> Next implementation stage: deepen verification for the now-live runtime—add golden tests for retrieval + LLM prompts, cover API key/rate-limit branches, and run the end-to-end integration test against the real backend.

### Vector search configuration (Vertex AI Matching Engine)
- **Index type:** Tree-AH with dot-product distance; match cosine behaviour by L2-normalizing every embedding (during upsert and query).
- **Dimensions:** Match `DATAPOINTS_DIMENSIONS`/`DATAPOINTS_MODEL` (3,072 with `gemini-embedding-001`, 768 with the `text-embedding-00x` family).
- **Replicas:** `ME_MIN_REPLICAS`/`ME_MAX_REPLICAS` control the min/max replica counts during `make gcp-index-deploy` (default 1/1); bump the max to allow autoscaling.
- **Tuning parameters:** `approximateNeighborsCount=100`, `leafNodeEmbeddingCount=1000`, and `leafNodesToSearchPercent=7`. Raise the neighbor count or search percent for higher recall (at the cost of latency), or lower them for faster, less exhaustive searches.
- **Provisioning:** Root Makefile targets (`make gcp-index-create`, `make gcp-index-endpoint-create`, `make gcp-index-deploy`, `make gcp-index-upsert`) generate the JSON metadata and call `gcloud` using env vars from `private/secrets`. Use `make gcp-index-list` to inspect existing indexes.

### Deployment/runtime stage (every query)

1. **Startup (Cloud Run container)**
   - Load env vars (`BUCKET_NAME`, `CHUNKS_PATH`, `PROJECT_ID`, `INDEX_ENDPOINT_ID`, etc.).
   - Load `chunks-<sha>.jsonl.gz` from GCS.
   - Build in-memory BM25 inverted index (tokenize chunks, compute idf, etc.).
2. **Query flow**
   - **First-person normalization** (convert “Omer Reznik” → “I”).
   - **Role classification**: cheap heuristic/embedding sim → `role=infra` or `role=product`. Leave unclassified for mixed queries.
   - **Vector search (ANN)**: embed query, call Vertex Matching Engine, fetch ~50 candidates.
   - **BM25 scoring**: run query keywords against in-memory index; get lexical scores.
   - **Rerank/boost**:
     - Blend scores: `0.7 * ANN + 0.3 * BM25 + role/topic boosts`.
     - If classified, boost chunks with matching role tag.
   - **Trim**: keep top ~8 chunks (aligns with intended architecture).
   - **Prompt LLM**: feed 8 chunks into Gemini Flash, generate strict, grounded first-person answer.
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
