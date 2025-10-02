# Version 5

## Backend
- Wire retrieval in `api/retrieval.py`: implement `embed_query`, `search_vector_store`, `apply_filters_and_boosting`, and `build_context_prompt`.
- Wire LLM call in `api/llm.py::call_gemini_flash` with limits from settings.
- Replace `/chat` `NotImplementedError` with real response. Add structured success logs.
- Confirm rate limits persistence if scaling beyond one instance.

## Ingestion
- Enhance `jobs/pack_and_push.py` to embed and upsert vectors to Vertex AI Matching Engine.
- Emit a side-store manifest and checksum for the `CHUNKS_PATH` artifact.
- Add `make ingest` target.

## Frontend
- Confirm starters and input disable logic when backend is down.
- Ensure explicit error toasts or banners on fetch failures and 503s.
- Keep the conversation scroll independent of the main page size.
- Remove any unused “Local backend” boxes if present.

## Tests
- Add golden tests for prompt builder and retrieval selection.
- Add API key missing/bad tests (401) and rate limits (429).
- Add integration tests for real backend once wired.

## Deployment
- Verify Cloud Run deployment and CORS with Hosting origin.
- Provision Vertex resources and document teardown.
- Keep budget alerts and logging hygiene.

References to prior docs for context: fileciteturn0file2 fileciteturn0file1

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
  - In `api.main.on_startup`, load the `CHUNKS_PATH` side store from `BUCKET_NAME` and initialize clients, then set `READY=True`.
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
