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

## Ingestion overview
The ingestion pipeline validates persona content, chunks it, embeds it, and uploads the resulting artifacts to GCS and Matching Engine for retrieval.

## Data flow
**Mock path (available for local dev)**
1. Client POSTs `/chat` with `{ "question": "..." }`.
2. Question is normalized to first person.
3. Service returns a deterministic answer with a dummy citation and usage.

**Real path**
1. Cloud Run API loads the side store object `CHUNKS_PATH` from `BUCKET_NAME` (GCS) at startup.
2. User question goes to backend, embed query, Vector Search top K 8, apply mild boosting and filters.
3. Build a strict grounded prompt, call Gemini Flash, return `{answer, citations, usage}`.

## Why this design works
- **Chunks** = source of truth, always recoverable.
- **Vertex AI Vector Search** = scalable semantic ANN engine.
- **BM25** = cheap keyword precision, especially for acronyms, IDs, rare terms.
- **Hybrid retrieval** = best of both worlds, with reranking and role boosts.
- **Sidecar store in GCS** = portable, versioned artifacts.
- **Runtime classification** = answers stay persona-consistent but context-aware.

See [`DATA_DESIGN_RATIONALE.md`](./DATA_DESIGN_RATIONALE.md) for discussion.

## Security
- Access keys live in Firestore collection `access_keys` with `key_hash` (bcrypt), `key_fingerprint` (SHA-256), `expires_at`, `revoked`, and optional labels/usage caps.
  - Rationale: Firestore is a good fit for low-ops, low-traffic access control metadata (expiry/revoke/usage caps) with straightforward admin workflows, and it provides durable, shared state across Cloud Run instances.
- `/auth/key-login` enforces rate limits before bcrypt (in-memory today).
- `/chat` requires auth and rate limits per access key (no per-IP limiting on `/chat`).
- Rate limiting is per-instance (in-memory). This is fine for a single Cloud Run instance, but must move to a shared store (Redis/Firestore) to be reliable under multi-instance scaling.
- Cookie sessions: `/auth/key-login` can set an HttpOnly cookie; logout clears the cookie only (no server-side session invalidation). Future enhancement: revoking an access key should immediately invalidate existing sessions.
- Operational note: if Firebase Hosting and Cloud Run are on different origins (for example `*.web.app` and `*.run.app`), browsers may treat session cookies as cross-site and apply stricter rules.
- CORS allowlist: localhost and your Hosting origin built from `PROJECT_ID`.

### Components
- **Backend**: FastAPI with two apps.
  - `api.mock:app` for local dev, deterministic answers.
  - `api.main:app` real mode: loads chunk side store on startup, runs hybrid retrieval + Gemini Flash; `/chat` returns 503 when not ready or when downstream services fail.
- **Frontend**: Next.js app in `web/` with starter prompts and a fixed layout. Cold start: min instances 0. Shows “Warming up the API… usually a few seconds.” until `/health` is ready. Verify disabled states when the backend is down, and independent scroll for the conversation pane.
- **Jobs**: `jobs/pack_and_push.py` to validate and package JSONL chunks.
- **Tests**: pytest suite focused on the mock app, plus opt-in integration tests that require a running real backend with valid GCP creds and data.
