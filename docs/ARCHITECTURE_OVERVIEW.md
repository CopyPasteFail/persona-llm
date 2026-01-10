# ARCHITECTURE_OVERVIEW

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

## Container registry choice
- Use Artifact Registry in the same region as Cloud Run to keep image pulls on Google’s network (no intra-GCP egress) and avoid Docker Hub rate/availability issues.
- IAM stays in GCP (no Docker Hub tokens), with audit logs and org policies applied uniformly across projects and environments.
- One registry works for Cloud Build, CI, Cloud Run, and GKE; tagging per environment fits the same workflow.
- Cost for the current `persona-backend:local` image (~0.212 GB) is $0/month because the first 0.5 GB is free; even 1 GB of images is only about $0.10/month.

## Ingestion steps
1. Convert CV .docx to JSONL chunks using ChatGPT with max ~450 tokens per chunk. Store in the private repo only
2. Validate the JSONL against the schema, splits long entries, add metadata fragments, and upload the `chunks-<sha>.jsonl.gz` to GCS for the backend side store.
3. Load the same chunks, call Vertex AI embedding to embed each fragment, and write a JSONL ready for Matching Engine upserts.

Chunks are validated using [`chunk.schema.json`](../backend/schema/chunk.schema.json).  
Field-level explanations and rationale are documented in [`SCHEMA.md`](SCHEMA.md).

## Data flow
**Mock path (available for local dev)**
1. Client POSTs `/chat` with `{ "question": "..." }`.
2. Question is normalized to first person.
3. Service returns a deterministic answer with a dummy citation and usage.

**Real path**
1. Cloud Run API loads the side store object `CHUNKS_PATH` from `BUCKET_NAME` (GCS) at startup.
2. User question goes to backend, embed query, Vector Search top K 8, apply mild boosting and filters.
3. Build a strict grounded prompt, call Gemini Flash, return `{answer, citations, usage}`.

## Security
- Access keys live in Firestore collection `access_keys` with `key_hash` (bcrypt), `key_fingerprint` (SHA-256), `expires_at`, `revoked`, and optional labels/usage caps.
- `/auth/key-login` enforces rate limits before bcrypt: 10 attempts per 10 minutes per IP and 5 per fingerprint (in-memory today; `/chat` requires the bearer token issued by key-login).
- `/chat` keeps existing per-IP limits: 10 per minute and 100 per day.
- Rate limiting is per-instance (in-memory). This is fine for a single Cloud Run instance, but must move to a shared store (Redis/Firestore) to be reliable under multi-instance scaling.
- CORS allowlist: localhost and your Hosting origin built from `PROJECT_ID`.

### Components
- **Backend**: FastAPI with two apps.
  - `api.mock:app` for local dev, deterministic answers.
  - `api.main:app` real mode: loads chunk side store on startup, runs hybrid retrieval + Gemini Flash; `/chat` returns 503 when not ready or when downstream services fail.
- **Frontend**: Next.js app in `web/` with starter prompts and a fixed layout. Cold start: min instances 0. Shows “Warming up the API… usually a few seconds.” until `/health` is ready. Verify disabled states when the backend is down, and independent scroll for the conversation pane.
- **Jobs**: `jobs/pack_and_push.py` to validate and package JSONL chunks.
- **Tests**: pytest suite focused on the mock app, plus opt-in integration tests that require a running real backend with valid GCP creds and data.
