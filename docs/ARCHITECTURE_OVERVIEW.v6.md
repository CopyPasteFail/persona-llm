# ARCHITECTURE_OVERVIEW v6

## Goal
A reusable public showcase where people can query a "persona" LLM representing a human. Answers are grounded in a provided dataset, for example a CV, using Vertex AI Vector Search. Priorities: low cost, low ops, transparent design.

## Stack
- Frontend: Next.js static export, hosted on Firebase Hosting
- Backend: FastAPI on Cloud Run
- Vector search: Vertex AI Matching Engine (Tree-AH, cosine)
- Embeddings: text-embedding-004 (768d)
- Side store: JSONL gzip in GCS bucket, loaded at startup
- LLM: Gemini 2.0 Flash with strict grounding and short answer style
- Monitoring: Cloud Logging and Cloud Monitoring metrics. Budget alerts only

## Ingestion steps
1. Convert CV .docx to JSONL chunks using ChatGPT with max ~450 tokens per chunk. Store in the private repo only
2. Ingestion job (pack_and_push.py): validate, embed, upsert to Vector Search, gzip JSONL, upload to GCS

## Data flow
**Mock path (active today)**
1. Client POSTs `/chat` with `{ "question": "..." }`.
2. Question is normalized to first person.
3. Service returns a deterministic answer with a dummy citation and usage.

**Intended real path (not implemented)**
1. Cloud Run API loads side store from `CHUNKS_URI` (GCS) at startup.
2. User question goes to backend, embed query, Vector Search top K 8, apply mild boosting and filters.
3. Query flow: , call Gemini Flash with strict grounding, return structured answer

## Security
- `x-api-key` required on real app paths. Rate limits per IP: 10 per minute, 100 per day.
- CORS allowlist: localhost and your Hosting origin built from `PROJECT_ID`.

## Environments and variables
Environment variables detected in code:
- `API_KEY`: used in backend/api/settings.py
- `CHUNKS_URI`: used in backend/api/settings.py
- `DEPLOYED_INDEX_ID`: used in backend/api/settings.py
- `PRIVATE_DIR`: used in backend/api/settings.py
- `INDEX_ENDPOINT_ID`: used in backend/api/settings.py
- `MAX_INPUT_TOKENS`: used in backend/api/settings.py
- `MAX_OUTPUT_TOKENS`: used in backend/api/settings.py
- `NEXT_PUBLIC_API_URL`: used in frontend/web/components/Layout.tsx, frontend/web/pages/index.tsx, frontend/web/utils/api.ts
- `PERSONA_MAX_WORDS`: used in backend/api/settings.py
- `PERSONA_NAME`: used in backend/api/settings.py
- `PROJECT_ID`: used in backend/api/settings.py
- `REGION`: used in backend/api/settings.py
- `REQ_TIMEOUT_MS`: used in backend/api/settings.py

## Deployment
- Cloud Run and Firebase Hosting instructions are kept, not verified in code.
- Real mode will not work until retrieval, vector search, and LLM calls are implemented.

## Repo layout

### Components
- **Backend**: FastAPI with two apps.
  - `api.mock:app` for local dev, deterministic answers.
  - `api.main:app` skeleton where `/chat` returns 503 until real retrieval and LLM are wired.
- **Frontend**: Next.js app in `web/` with starter prompts and a fixed layout. Cold start: min instances 0. Shows “Warming up the API… usually a few seconds.” until `/health` is ready. Verify disabled states when the backend is down, and independent scroll for the conversation pane.
- **Jobs**: `jobs/pack_and_push.py` to validate and package JSONL chunks.
- **Tests**: pytest suite focused on the mock app. One integration test expects a real backend and will fail until implemented.
