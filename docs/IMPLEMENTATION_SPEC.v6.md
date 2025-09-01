# IMPLEMENTATION_SPEC v5

## Repos
- Public mono-repo: this zip contains both backend and frontend. The backend is in `api/` and jobs under `jobs/`. The frontend is in `web/`. A private folder for secrets may be referenced during runtime, not committed.

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
├── docs
│   ├── ARCHITECTURE_OVERVIEW.v6.md
│   ├── CHANGELOG.md
│   ├── GAPS_AND_TODOS.md
│   ├── IMPLEMENTATION_SPEC.v6.md
│   ├── RATIONALE.md
│   ├── previous_versions
│   │   ├── ARCHITECTURE_OVERVIEW.v3.md
│   │   ├── ARCHITECTURE_OVERVIEW.v4.md
│   │   ├── ARCHITECTURE_OVERVIEW.v5.md
│   │   ├── IMPLEMENTATION_SPEC.v3.md
│   │   ├── IMPLEMENTATION_SPEC.v4.md
│   │   └── IMPLEMENTATION_SPEC.v5.md
│   └── prompts
│       ├── Allign Specs and Architechture Documents.txt
│       └── generate.sh
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
│       ├── backend.env
│       └── frontend.env
└── scripts
    └── link-private.sh
```

## Environment variables
List of variables discovered in code. Values must be provided via your private folder or environment. Placeholders should not be used in production.
- `API_KEY`: used in backend/api/settings.py
- `CHUNKS_URI`: used in backend/api/settings.py
- `DEPLOYED_INDEX_ID`: used in backend/api/settings.py
- `ENV_DIR`: used in backend/api/settings.py
- `INDEX_ENDPOINT_ID`: used in backend/api/settings.py
- `MAX_INPUT_TOKENS`: used in backend/api/settings.py
- `MAX_OUTPUT_TOKENS`: used in backend/api/settings.py
- `NEXT_PUBLIC_API_URL`: used in frontend/web/components/Layout.tsx, frontend/web/pages/index.tsx, frontend/web/utils/api.ts
- `PERSONA_MAX_WORDS`: used in backend/api/settings.py
- `PERSONA_NAME`: used in backend/api/settings.py
- `PROJECT_ID`: used in backend/api/settings.py
- `REGION`: used in backend/api/settings.py
- `REQ_TIMEOUT_MS`: used in backend/api/settings.py

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
- `api/retrieval.py`: first-person normalization implemented. Retrieval stubs (`embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt`) not implemented.
- `api/llm.py`: `build_llm_prompt` returns the strict format. `call_gemini_flash` not implemented.

## Ingestion jobs
- `jobs/pack_and_push.py` validates JSONL against `schema/chunk.schema.json`, splits long texts, writes `chunks-<sha>.jsonl.gz`. Prints a `gs://` URI if a bucket is configured in `config/settings.yaml`. No embedding or upsert yet.

## Ingestion and Retrieval Design

### Overall scheme

- **Two CVs**: one infra (DevOps/SRE/Platform) and one product (PM/TPM/PO).
- **Tags**:
  - Always add `role:infra` or `role:product`.
  - Optionally add `topic:*` for precision (`topic:kubernetes`, `topic:roadmap`).
- **Chunks**: store as JSONL lines with text + metadata; keep gzipped in GCS (`CHUNKS_URI`) as a sidecar store.
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
   - Embed each chunk with `text-embedding-004`.
   - Upsert `{vector, metadata}` to Vertex AI Matching Engine.

### Deployment/runtime stage (every query)

1. **Startup (Cloud Run container)**
   - Load `chunks-<sha>.jsonl.gz` from GCS.
   - Build in-memory BM25 inverted index (tokenize chunks, compute idf, etc.).
   - Load env vars (`CHUNKS_URI`, `PROJECT_ID`, `INDEX_ENDPOINT_ID`, etc.).
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

See RATIONALE.md §3 for discussion.

## Frontend behavior
- Frontend folder not found under `web/`.

## Makefile targets (root)
- BACKEND_ENV
- FRONTEND_ENV
- build
- clean
- clean-all
- dev
- fe-install
- install
- mock
- require-private

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

## Provenance
This v5 spec updates v4 to match the merged mono-repo and current code layout. Prior v4 references two repos and unverified frontend details.

References to prior docs: fileciteturn0file3 fileciteturn0file1