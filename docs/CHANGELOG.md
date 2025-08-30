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
