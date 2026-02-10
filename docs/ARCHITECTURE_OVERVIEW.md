# ARCHITECTURE_OVERVIEW
_Version 6.0.04_

## Goal
A reusable public showcase where people can query a "persona" LLM representing a human. Answers are grounded in a provided dataset, for example a CV, using Vertex AI Vector Search. Priorities: low cost, low ops, transparent design.

## Stack
- Frontend: Next.js static export, hosted on Firebase Hosting
- Backend: FastAPI on Cloud Run
- Vector search: Vertex AI Matching Engine (Tree-AH, dot product; use unit-normalized vectors for cosine equivalence)
- Embeddings: gemini-embedding-001 (3072d) via Vertex AI (switchable via `DATAPOINTS_MODEL`)
- Side store: JSONL gzip in GCS bucket, loaded at startup
- LLM: Gemini 2.0 Flash with strict grounding and short answer style
- Monitoring: Cloud Logging and Cloud Monitoring metrics. Budget alerts only

## Ingestion steps
1. Convert CV .docx to JSONL chunks using ChatGPT with max ~450 tokens per chunk. Store in the private repo only
2. Validate the JSONL against the schema, splits long entries, add metadata fragments, and upload the `chunks-<sha>.jsonl.gz` to GCS for the backend side store.
3. Load the same chunks, call Vertex AI embedding to embed each fragment, and write a JSONL ready for Matching Engine upserts.

Chunks are validated using [`chunk.schema.json`](../backend/schema/chunk.schema.json).  
Field-level explanations and rationale are documented in [`SCHEMA.md`](SCHEMA.md).

## Data flow
**Mock path (active today)**
1. Client POSTs `/chat` with `{ "question": "..." }`.
2. Question is normalized to first person.
3. Service returns a deterministic answer with a dummy citation and usage.

**Real path**
1. Cloud Run API loads the side store object `CHUNKS_PATH` from `BUCKET_NAME` (GCS) at startup.
2. User question goes to backend, embed query, Vector Search top K 8, apply mild boosting and filters.
3. Query flow: , call Gemini Flash with strict grounding, return structured answer

## Security
- `x-api-key` required on real app paths. Rate limits per IP: 10 per minute, 100 per day.
- CORS allowlist: localhost and your Hosting origin built from `PROJECT_ID`.

### Components
- **Backend**: FastAPI with two apps.
  - `api.mock:app` for local dev, deterministic answers.
  - `api.main:app` skeleton where `/chat` returns 503 until real retrieval and LLM are wired.
- **Frontend**: Next.js app in `web/` with starter prompts and a fixed layout. Cold start: min instances 0. Shows “Warming up the API… usually a few seconds.” until `/health` is ready. Verify disabled states when the backend is down, and independent scroll for the conversation pane.
- **Jobs**: `jobs/pack_and_push.py` to validate and package JSONL chunks.
- **Tests**: pytest suite focused on the mock app. One integration test expects a real backend and will fail until implemented.
