# V4 to V5

- Repos merged into a single public repo with `api/`, `jobs/`, and `web/` in one tree. Prior v4 assumed separate repos. fileciteturn0file3 fileciteturn0file1
- Frontend now present under `web/`; previous docs marked frontend as unverified. Verified structure and environment files existence, functional details still require real backend. fileciteturn0file3
- Environment variables list updated based on code scanning. Placeholders still disallowed by settings loader.
- API contract remains minimal: `question` only. Real path returns 503 until retrieval and LLM are wired. fileciteturn0file3
- Ingestion job `pack_and_push.py` confirmed to only validate, split, and package JSONL, with a printed URI. No embeddings or upsert yet. fileciteturn0file3

## Areas
### Backend
- `api.main` remains a skeleton with `NotImplementedError` for `/chat`. Mock app is the supported path. fileciteturn0file3
- Rate limits, API key check, and CORS allowlist remain in place.
### Frontend
- Next.js app under `web/`. Keep disabled input behavior and independent scroll. Confirm error handling for 503 cases.
### Tests and tooling
- Tests target `api.mock`. Integration test for real backend remains failing until real mode is implemented. fileciteturn0file3
### Deployment
- Cloud Run and Firebase steps are retained but remain unverified in code. fileciteturn0file1

## Pointers to code
- Real app: `backend/api/main.py`
- Mock app: `backend/api/mock.py`
- Retrieval: `backend/api/retrieval.py`
- LLM: `backend/api/llm.py`
- Security: `backend/api/security.py`
- Settings: `backend/api/settings.py`
- Schema: `backend/schema/chunk.schema.json`
- Config example: `backend/config/settings.yaml.example`

# V3 to V4

Summary of changes from v3 docs to current v4. Prior versions referenced for context: fileciteturn0file0 fileciteturn0file1

## Backend API
- `/chat` real path now **returns 503** with `NotImplementedError` by design. v3 implied a working Gemini + Vertex path.
- Request schema **reduced** to `{"question": "..."}`. Legacy keys `role`, `year`, `tech` are ignored by `pydantic` instead of being used.
- First-person normalization implemented in `api/retrieval.py` and covered by tests.
- Deterministic mock app `api.mock` is the supported local flow.

## Retrieval and LLM
- `embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt` all **not implemented** and raise `NotImplementedError`.
- `call_gemini_flash` **not implemented**. `build_llm_prompt` returns the strict TLDR + bullets + wrap format.

## Ingestion
- `jobs/pack_and_push.py` now **only** validates, sentence-splits, gzips, and prints a URI. It does **not** embed or upsert to Vector Search.
- `config/settings.yaml.example` includes `bucket`, but there is no Makefile `ingest` target in the repo. v3 referenced `make ingest`.

## Env vars
- Settings loader rejects obvious placeholders. This was not explicit in v3.
- `PROJECT_ID` impacts CORS allowlist. Other GCP identifiers are presently unused by code.

## Tests and Tooling
- Test suite targets the **mock** app for health and contract checks.
- An integration test for the real backend exists but will fail until real mode is wired.
- Makefile includes `mock`, `run`, `test`, `test-voice`, `test-int`. No `ingest` target.

## Frontend
- Not reviewed in this pass. v3 content retained as unverified until `persona-llm-frontend` is provided.

## Deployment
- Cloud Run and Firebase steps remain **unverified**. Real path is not functional in code yet.
