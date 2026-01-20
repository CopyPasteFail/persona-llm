## Backend
- Strengthen retrieval in `api/retrieval.py`: expand tests around `embed_query`, `search_vector_store`, `apply_filters_and_boosting`, and `build_context_prompt`, and validate live client integration.
- Add unit tests for dataset cache loading (pointer, manifest validation, normalization guards) and local vector search scoring.
- Add regression tests + observability for the Gemini client (`api/llm.py::call_gemini_flash`): stubbed unit tests, usage parsing coverage, and explicit timeout/error handling.
- Rate limiting (enhancement): keep the FastAPI limiter for app-level protection and consistent behavior, add an edge/service-level limiter (Cloud Armor / API Gateway / load balancer) for stronger enforcement and DDoS resistance.
- Rate limiting (enhancement): move rate-limit storage to a shared store (Redis/Firestore; currently per-pod in-memory) for multi-instance deployments.
- Auth/session (enhancement): revoking an access key should immediately invalidate existing JWT sessions (not just block new logins).
- Auth/session (enhancement): Server-side session invalidation to mitigate stolen-token reuse. Currently, logout is cookie deletion only (no server-side session invalidation). Good enough for small invite-only access-key sharing, and keeps the system stateless.

## Ingestion
- (enhancement) Automate the upsert flow (or integrate with `pack_and_push`) if you want a single command to run the whole pipeline. Currently, `jobs/build_datapoints.py` now generates embeddings.
- Emit a side-store manifest and checksum for the `CHUNKS_PATH` artifact.
- (enhancement) Add a `make ingest` target that chains the steps above.
- (enhancement) CI-triggered dataset reloads after uploading a new `datasets/<version>/` folder.

## Frontend

## Tests
- LLM prompt builder (`api/llm.py`): missing tests for strict output instructions, context format, MAX_INPUT_TOKENS trimming, and usage parsing from Vertex responses. Decision: add unit coverage for `build_llm_prompt(...)`, trimming edge cases, and `_extract_usage`/`_usage_value`.
- Rate limiting (`api/security.py`): missing tests for `/chat` 429s after thresholds and `/auth/key-login` IP/fingerprint limits (before key verification). Decision: add rate-limit tests for both endpoints and ordering.
- Rate limiting tests: add coverage for `/chat` rate limiting in `api.main` (via `check_rate_limit_dependency`), since the mock app does not exercise `api.main`’s `/chat` path.
- Invalid/bad-key login (`api/auth.py`, `api/keys.py`): missing 401 coverage for wrong, revoked, expired, and overused keys, plus 429s for rate-limited login attempts. Decision: add negative auth tests to lock in these cases.
- Add integration tests for real backend once wired.

## Deployment
- Provision Vertex resources and document teardown.
- Keep budget alerts and logging hygiene.
- (enhancement) IAM-only ops endpoints via a separate service or gateway (keep current in-app `OPS_SECRET` for the public API).
