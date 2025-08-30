# V4

## Must-do to enable real mode
- Implement embeddings and vector search:
  - `api/retrieval.py::embed_query`.
  - `api/retrieval.py::search_vector_store`.
  - `api/retrieval.py::apply_filters_and_boosting`.
  - `api/retrieval.py::build_context_prompt`.
- Wire LLM call:
  - `api/llm.py::call_gemini_flash` with max tokens and low temperature.
- Startup loading:
  - In `api.main.on_startup`, load `CHUNKS_URI` side store and initialize clients, then set `READY=True`.
- Error handling:
  - Replace `NotImplementedError` path with proper 200 response and structured logging.

## Ingestion
- Extend `jobs/pack_and_push.py`:
  - Generate embeddings and upsert to Vertex AI Matching Engine.
  - Emit a consistent side-store manifest and checksum.
- Add `make ingest` target that runs the job with the repo’s schema and config.
- Document the end-to-end ingestion flow in `README.md` with a concrete example.

## Contract and schema
- Decide whether `role`, `year`, and `tech` filters are part of the API. If yes, re-introduce them in `ChatRequest` and implement filtering.
- Keep Pydantic `extra="ignore"` to avoid breaking older frontends.

## Security and limits
- Persist or shard rate-limits if multi-instance is planned. Current in-memory deques are per-pod only.
- Add structured logs on success paths with `request_id`, latency, selected chunk IDs, and token counts.
- Ensure secrets never hit logs. Keep placeholder validation in `api/settings.py`.

## Frontend
- Re-verify `persona-llm-frontend` once provided:
  - Starter prompts disabled state when backend is down.
  - Conversation scroll container and fixed layout.
  - Remove any unused “Local backend” UI boxes.
  - Confirm `NEXT_PUBLIC_API_URL` handling and error toasts for 503 cases.

## Tests
- Expand tests to cover failure cases on the real app:
  - 401 for missing or bad API key.
  - 429 for rate limits.
  - 503 propagation when downstream services fail.
- Add golden tests for the LLM prompt builder.
- Keep the existing voice normalization tests green.

## Deployment
- Finish Cloud Run wiring and verify startup path.
- Provision Vertex resources via Terraform or scripts and document teardown.
- Confirm Firebase Hosting config and CORS with the deployed domain.
