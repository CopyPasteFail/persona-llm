# IMPLEMENTATION_SPEC.v4.md

fileciteturn0file0

## Repos

- Private backend and ingestion: **<PERSONA_NAME>-llm-backend**.
- Public frontend showcase: **persona-llm-frontend**. **Not verified in current pass** because the frontend zip was not attached. Keep prior steps but treat them as unverified until code is reviewed.

### Current backend tree

```
./
  .env.example
  .gitignore
  Makefile
  README.md
  pytest.ini
  requirements.txt
  api/
    __init__.py
    llm.py
    main.py
    mock.py
    retrieval.py
    security.py
    settings.py
    types.py
    api/__pycache__/
      __init__.cpython-313.pyc
      __init__.cpython-313.pycZone.Identifier
      mock.cpython-313.pyc
      mock.cpython-313.pycZone.Identifier
      retrieval.cpython-313.pyc
      settings.cpython-313.pyc
      types.cpython-313.pyc
      types.cpython-313.pycZone.Identifier
  config/
    settings.yaml.example
  jobs/
    pack_and_push.py
  schema/
    chunk.schema.json
  tests/
    test_integration_real_backend.py
    test_normalize_question_punct.py
    test_persona_voice.py
    test_smoke.py
    tests/__pycache__/
      test_normalize_question_punct.cpython-313-pytest-8.2.2.pyc
      test_persona_voice.cpython-313-pytest-8.2.2.pyc
      test_smoke.cpython-313-pytest-8.2.2.pyc
      test_smoke.cpython-313-pytest-8.2.2.pycZone.Identifier
      test_validation.cpython-313-pytest-8.2.2.pyc
      test_validation.cpython-313-pytest-8.2.2.pycZone.Identifier
```

Notes:
- `api.main` is a real-mode skeleton that currently raises `NotImplementedError` on `/chat`.
- `api.mock` is the working local path used by tests. It returns a deterministic first-person answer.

## Env Vars (private repo only at runtime)

Loaded via `python-dotenv` in `api/settings.py`. Required variables and usage:
- `PROJECT_ID`
- `REGION`
- `INDEX_ENDPOINT_ID`
- `DEPLOYED_INDEX_ID`
- `CHUNKS_URI`
- `API_KEY`
- `MAX_INPUT_TOKENS`
- `MAX_OUTPUT_TOKENS`
- `REQ_TIMEOUT_MS`

Usage in code:
- `PROJECT_ID`: used to shape CORS allowlist in `api/main.py`.
- `REGION`, `INDEX_ENDPOINT_ID`, `DEPLOYED_INDEX_ID`, `CHUNKS_URI`: referenced in settings and deployment docs. **Not used by code paths yet**.
- `API_KEY`: compared in `api/security.py` by the `verify_api_key` dependency.
- `MAX_INPUT_TOKENS`, `MAX_OUTPUT_TOKENS`, `REQ_TIMEOUT_MS`: read in settings, used by `api/main.py` when building the LLM call shape. Real LLM call is not wired.

Placeholders are rejected by `api/settings.py` so production cannot boot with junk values.

## Data Schema

Validation file: `schema/chunk.schema.json`.
Key constraints:
- Require `id`, `text`, `metadata`.
- In `metadata`: require `employer`, `year`, `tech`, `section`, `type`.
- Roles: either `role` string, or both `roles` array and `role_primary` string.
- `type` is one of `achievement` or `experience`.

Example record:

```json
{{
  "id": "cv:2024:nexyte:devops-engineer:001",
  "text": "Context text...",
  "metadata": {{
    "employer": "Nexyte",
    "roles": ["Founder","Product Manager"],
    "role_primary": "Product Manager",
    "year": 2024,
    "start_year": 2023,
    "end_year": 2025,
    "tech": ["kubernetes","ansible"],
    "type": "achievement",
    "section": "experience"
  }}
}}
```

## Ingestion (`jobs/pack_and_push.py`)

What it **does today**:
- Validates `data/cv_chunks.jsonl` against `schema/chunk.schema.json`.
- Splits overly long `text` by sentence boundaries, target ~2200 chars.
- Assigns deterministic IDs when missing.
- Writes `chunks-<sha>.jsonl.gz` to the working directory and **prints a URI**:
  - `gs://<bucket>/chunks-<sha>.jsonl.gz` if `bucket` is set in `config/settings.yaml`.
  - `file://...` absolute path otherwise.

What it **does not do yet**:
- No embedding.
- No upsert to Vertex AI Vector Search.
- No side-store upload beyond the simple GCS write implied by the printed URI.

Run:

```bash
python jobs/pack_and_push.py --settings config/settings.yaml --schema schema/chunk.schema.json --input data/cv_chunks.jsonl
```

Config example (`config/settings.yaml.example`):

```yaml
project_id: YOUR_PROJECT
region: europe-west1
bucket: <PERSONA_NAME>-llm-side-store
index_endpoint_id: projects/XXX/locations/europe-west1/indexEndpoints/NNN
deployed_index_id: <PERSONA_NAME>-llm-deployed
```

## Backend API (FastAPI)

### Apps
- **Real skeleton**: `api.main:app`. Sets `READY=True` on startup, exposes `/health`, and wires dependencies for API key and rate limits. The `/chat` handler normalizes the question to first person, stubs retrieval and LLM calls, then raises `NotImplementedError` which is returned as HTTP 503.
- **Mock app**: `api.mock:app`. Used by tests and local dev. Returns a deterministic first-person response with a dummy citation and usage counts.

### Endpoints
- `GET /health` returns `{{"ready": <bool>}}`.
- `POST /chat` accepts JSON body and returns structured JSON.

Request schema (Pydantic v2, `api/types.py`):
```json
{{ "question": "string, required" }}
```
Notes: Extra fields are ignored by Pydantic config, so legacy `role`, `year`, or `tech` keys are accepted but ignored.

Response schema:
```json
{{ "answer": "string",
   "citations": [{{"id":"string","text":"string?"}}],
   "usage": {{"input_tokens": 0, "output_tokens": 0}} }}
```

### Security
- Header: `x-api-key` must match `API_KEY`. Implemented in `api/security.py`.
- Rate limits per IP on `/chat`: 10 per minute, 100 per day.
- CORS allowlist: `http://localhost:3000` and `https://<PROJECT_ID>.web.app`.

### Run locally

Mock app (recommended for dev):
```bash
uvicorn api.mock:app --port 8080
# Health
curl -s http://localhost:8080/health | jq .
# Chat
curl -s -X POST http://localhost:8080/chat -H 'content-type: application/json' -d '{{"question":"demo"}}' | jq .
```

Real skeleton (will return 503 on `/chat` until wired):
```bash
uvicorn api.main:app --port 8000
curl -s -X POST http://localhost:8000/chat -H 'x-api-key: $API_KEY' -H 'content-type: application/json' -d '{{"question":"demo"}}' | jq .
```

### Retrieval and LLM pipeline, as coded now
- `api/retrieval.py`: `normalize_question_for_first_person(...)` is implemented and tested. Real integrations `embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt` all raise `NotImplementedError`.
- `api/llm.py`: `build_llm_prompt(...)` returns a strict message format. `call_gemini_flash(...)` raises `NotImplementedError`.
- `api/main.py`: calls the above and then raises `NotImplementedError` to avoid silent success.

## Frontend (Next.js static plus Firebase Hosting)

**Not verified in current pass.** The frontend repo zip was not attached, so the prior v3 description is kept as a placeholder. Re-validate once the code is available. Items to confirm:
- Starter prompts behavior and disabled states when the backend is down.
- Independent scroll for the conversation box and fixed main page size.
- Removal of any “Local backend” UI boxes.
- Environment variable `NEXT_PUBLIC_API_URL` usage and fetch code paths.

fileciteturn0file0

## Monitoring and cost

- Logging uses Python `logging` with structured `dict` payloads in error paths. Extend to include request id, latency, retrieved ids, and token counts across success paths.
- Budget alerts on GCP are assumed. **Not verified in code.**

## Tests

Available tests in `tests/`:
- `test_smoke.py` against `api.mock` app: health and `/chat` contract.
- `test_persona_voice.py`: ensures first-person normalization and answer shape.
- `test_normalize_question_punct.py`: regression tests for apostrophes, URLs, emails, paths.
- `test_integration_real_backend.py` (marked `@pytest.mark.integration`): assumes a real backend on `:8000` returning first-person answers. **This will fail until real mode is implemented.**

Useful Makefile targets:
- `make install`
- `make mock`
- `make run`  (real skeleton)
- `make test`
- `make test-voice`
- `make test-int`

## Deployment

Keep the v3 steps, but mark unverified items. Real production flow is **not wired in code yet**.

One time GCP (unverified):
- Create private GCS bucket for side store.
- Create Matching Engine index and endpoint.
- Create Cloud Run service account with `aiplatform.user`, `aiplatform.viewer`, Matching Engine read, and `storage.objectViewer` on the bucket.
- Budget alerts at 90 and 100 percent.

First ingestion (partially wired):
- Prepare `data/cv_chunks.jsonl`.
- Run `jobs/pack_and_push.py` to produce `chunks-<sha>.jsonl.gz` and upload to the bucket.
- **Embedding and upsert to Vector Search are not implemented.**

Cloud Run deploy (unverified and will not be functional until real mode is implemented):
```bash
gcloud run deploy ask-<PERSONA_NAME>-api --source ./api --region $REGION --service-account $RUNTIME_SA   --set-env-vars PROJECT_ID=$PROJECT_ID,REGION=$REGION,INDEX_ENDPOINT_ID=$IE,DEPLOYED_INDEX_ID=$DI,CHUNKS_URI=$URI,API_KEY=$API_KEY,MAX_INPUT_TOKENS=3000,MAX_OUTPUT_TOKENS=180,REQ_TIMEOUT_MS=20000
```

Firebase Hosting (unverified):
- Ensure `web/.env.production` points to the Cloud Run URL.
- Build and deploy Hosting.

## Python 3.13 compatibility

- Use `uvicorn` without extras. Avoid `uvloop` and `httptools` unless wheels are confirmed.
- `orjson` is optional. Not required by code today.

## .gitignore guidance

- Private repo: ignore `.env` and `.env.*`, ignore `config/settings.yaml`, commit `config/settings.yaml.example`.
- Public repo: ignore `web/.env.local`, commit `web/.env.local.example`, commit `web/.env.production` with placeholders only.
