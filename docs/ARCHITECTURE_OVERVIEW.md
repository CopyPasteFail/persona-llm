# ARCHITECTURE_OVERVIEW

## Goal
A reusable public showcase where people can query a "persona" LLM representing a human. Answers are grounded in a provided dataset, for example a CV, using Vertex AI Vector Search. Priorities: low cost, low ops, transparent design.

Terminology: see [GLOSSARY.md](./GLOSSARY.md).

## Stack
- Frontend: Next.js static export, hosted on Firebase Hosting
- Backend: FastAPI on Cloud Run
- Vector search: local in-process cosine search by default, optional Vertex AI Matching Engine (Tree-AH, dot product; use unit-normalized vectors for cosine equivalence)
- Embeddings: Vertex AI embedding model configured via `DATAPOINTS_MODEL` (default `text-embedding-004` at 768d; `gemini-embedding-001` is 3072d)
- Dataset cache: versioned dataset folder in GCS + pointer file (atomic swap), loaded into memory at startup
- LLM: Gemini 2.0 Flash with strict grounding and short answer style
- Monitoring: Cloud Logging and Cloud Monitoring metrics. Budget alerts only

## Ingestion overview
The ingestion pipeline validates persona content, chunks it, embeds it, and uploads the resulting artifacts to GCS and Matching Engine for retrieval.

## Data flow
**Mock path (available for local dev)**
1. Client POSTs `/chat` with `{ "question": "..." }`.
2. Question is normalized to first person.
3. Service returns a deterministic answer with a dummy citation and usage (default), or a real Vertex response when `LLM_BACKEND=vertex`.

**Real path**
1. Cloud Run API loads `datasets/current.json` from `BUCKET_NAME`, resolves the version folder, and caches `datapoints.jsonl`, `chunks.jsonl.gz`, and `manifest.json` in-process.
2. User question goes to backend, embed query, vector search top K 8 (local by default or Matching Engine when configured), apply mild boosting and filters.
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
- CORS (Cross-Origin Resource Sharing) tells browsers which frontend origins are allowed to call the API.
  - Allowlist: localhost and your Hosting origin built from `PROJECT_ID`.
- Cloud Run stays publicly accessible for the frontend; ops endpoints (`/ops/vector/status`, `/ops/vector/reload`) are protected with `x-ops-secret` when `OPS_AUTH` is enabled. Local dev can bypass with `OPS_AUTH=disabled`.

## Cloud Run scaling notes
Important considerations:
- Service-level vs. revision-level: Cloud Run allows min/max instances at both the service level and the revision level.
  - For max-instances, the effective limit is the lower of the service-level and revision-level values.
  - For min-instances, the effective value is the higher of the service-level and revision-level values.
- Console display: the service details page typically shows service-level scaling. To set global defaults without creating a new revision, use `gcloud run services update` with `--min-instances` and `--max-instances` (note the service-level command and flags).
- Default min=0: when min is 0, Cloud Run does not store an annotation, so it will not appear in `gcloud run services describe` output or in GCP console.

To view deployed services in the console, open:
https://console.cloud.google.com/run/services
Then choose the project.

### Components
- **Backend**: FastAPI with two apps.
  - `api.mock:app` for local dev, deterministic answers.
  - `api.main:app` integrated mode: loads versioned dataset cache on startup, runs hybrid retrieval + Gemini Flash; `/chat` returns 503 when not ready or when downstream services fail.
  - Ops endpoints for cache status + reload live on the same service.
- **Frontend**: Next.js app in `web/` with starter prompts and a fixed layout. Cold start: min instances 0. Shows “Warming up the API… usually a few seconds.” until `/health` is ready. Verify disabled states when the backend is down, and independent scroll for the conversation pane.
- **Jobs**: `jobs/pack_and_push.py` to validate and package JSONL chunks.
- **Tests**: pytest suite focused on the mock app, plus opt-in integration tests that require a running integrated backend with valid GCP creds and data.
