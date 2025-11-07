# IMPLEMENTATION_SPEC
_Version 6.0.03_

## Repos
- Public mono-repo: Contains both backend and frontend. The backend is in `api/` and jobs under `jobs/`. The frontend is in `web/`. A private folder for secrets may be referenced during runtime, not committed.

### Current repo tree (trimmed)
```
.
├── LICENSE
├── Makefile
├── README.md
├── backend
│   ├── Makefile
│   ├── README.md
│   ├── api
│   │   ├── __init__.py
│   │   ├── llm.py
│   │   ├── main.py
│   │   ├── mock.py
│   │   ├── retrieval.py
│   │   ├── security.py
│   │   ├── settings.py
│   │   └── types.py
│   ├── jobs
│   │   └── pack_and_push.py
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── schema
│   │   └── chunk.schema.json
│   └── tests
│       ├── conftest.py
│       ├── test_integration_real_backend.py
│       ├── test_normalize_question_punct.py
│       ├── test_persona_voice.py
│       └── test_smoke.py
├── frontend
│   ├── README.md
│   ├── firebase.json
│   ├── package-lock.json
│   ├── package.json
│   └── web
│       ├── components
│       │   └── Layout.tsx
│       ├── next-env.d.ts
│       ├── next.config.mjs
│       ├── package.json
│       ├── pages
│       │   ├── _app.tsx
│       │   └── index.tsx
│       ├── postcss.config.mjs
│       ├── public
│       │   ├── android-chrome-192x192.png
│       │   ├── android-chrome-512x512.png
│       │   ├── apple-touch-icon.png
│       │   ├── favicon-16x16.png
│       │   ├── favicon-32x32.png
│       │   ├── favicon.ico
│       │   └── site.webmanifest
│       ├── styles
│       │   └── globals.css
│       ├── tailwind.config.ts
│       ├── tsconfig.json
│       └── utils
│           ├── api.ts
│           └── types.ts
├── private-template
│   ├── persona
│   │   ├── assets
│   │   ├── persona.yaml
│   │   ├── starters.json
│   │   └── vector-seed
│   └── secrets
│       ├── common.env
│       ├── backend.env
│       └── frontend.env
└── scripts
    └── link-private.sh
```

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
  - `API_KEY`: Key for calling Vertex AI endpoints.
  - `MAX_INPUT_TOKENS`: Input context budget for LLM calls.
  - `MAX_OUTPUT_TOKENS`: Output budget for LLM calls.
  - `REQ_TIMEOUT_MS`: Request timeout in milliseconds.

- **Frontend variables (in `frontend/web/.env.local`):**
  - `NEXT_PUBLIC_API_URL`: URL of the backend (e.g. `http://localhost:8080` during local dev).

`settings.py` uses `python-dotenv` to load `${PRIVATE_DIR}/secrets/common.env` followed by `${PRIVATE_DIR}/secrets/backend.env` into the process environment before FastAPI starts. Missing required values will raise validation errors on startup.


## Backend API
### Endpoints
- `GET /health` returns `{ "ready": <bool> }`.
- `POST /chat` accepts JSON and returns structured JSON. The real backend currently returns 503 until retrieval and LLM are wired.
### Request schema
- `question`: str
### Response schema
- `answer`: str
- `citations`: List[Citation]
- `usage`: Usage

### Minimal examples
Mock app (if running at port 8000):
```bash
curl -s http://localhost:8000/health | jq .
curl -s -X POST http://localhost:8000/chat -H 'content-type: application/json' -d '{"question":"demo"}' | jq .
```

## Retrieval and LLM pipeline
- `api/retrieval.py`: first-person normalization and retrieval pipeline (`embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt`) implemented; focus now on tests, tuning, and live Vertex integration.
- `api/llm.py`: `build_llm_prompt` returns the strict format. `call_gemini_flash` not implemented.

## Ingestion jobs
- `jobs/pack_and_push.py` validates JSONL against `schema/chunk.schema.json`, and if any bullet **or paragraph** exceeds ~2.2k characters (~450 tokens), splits it by sentence boundaries (never mid-sentence). Writes `chunks-<sha>.jsonl.gz` with enriched metadata and prints a `gs://` URI if a `bucket:` is provided in a YAML file passed with `--settings`.
- `jobs/build_datapoints.py` loads the same chunk file, calls Vertex AI `text-embedding-004` to generate vectors, and emits a newline-delimited JSON file ready for `gcloud ai index-endpoints upsert-datapoints`. Embedding calls are batched in groups of 16 (`DATAPOINTS_BATCH_SIZE` in the env) to minimize network round trips without risking payload limits.

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
   - Split CV docs into ~450-token chunks.
   - Attach `role` + optional `topic` tags.
   - Validate against `chunk.schema.json`.
2. **Packaging**
   - Write `chunks-<sha>.jsonl.gz`.
   - Upload to GCS (sidecar store).
3. **Embedding + upsert** *(not yet wired)*
   - Embed each chunk with `text-embedding-004` (outputs 3,072-dimensional vectors).
   - Upsert `{vector, metadata}` to Vertex AI Matching Engine.

### Vector search configuration (Vertex AI Matching Engine)
- **Index type:** Tree-AH with dot-product distance; match cosine behaviour by L2-normalizing every embedding (during upsert and query).
- **Dimensions:** 3,072 to match `text-embedding-004`.
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

### Why this design works

- **Chunks** = source of truth, always recoverable.
- **Vertex AI Vector Search** = scalable semantic ANN engine.
- **BM25** = cheap keyword precision, especially for acronyms, IDs, rare terms.
- **Hybrid retrieval** = best of both worlds, with reranking and role boosts.
- **Sidecar store in GCS** = portable, versioned artifacts.
- **Runtime classification** = answers stay persona-consistent but context-aware.

See [`DATA_DESIGN_RATIONALE.md`](./DATA_DESIGN_RATIONALE.md) for discussion.

## Frontend behavior
- Frontend present under `frontend/web/`.

## Tests
- Python tests under `tests/`:
  - `backend/tests/conftest.py`
  - `backend/tests/test_integration_real_backend.py`
  - `backend/tests/test_normalize_question_punct.py`
  - `backend/tests/test_persona_voice.py`
  - `backend/tests/test_smoke.py`

## Deployment notes
- Cloud Run and Firebase steps exist in docs, but are not verified in code. Keep them as unverified. Ensure CORS allowlist uses your deployed Hosting origin.

## Compatibility and tooling
- Python 3.13 compatible, avoid optional wheels unless verified. `orjson` is optional.
