# ARCHITECTURE_OVERVIEW.v4.md

fileciteturn0file1

## Goal
A low-cost, low-ops “persona” QA demo that answers in first person and stays grounded in a curated dataset. Keep the design transparent and simple.

## Stack

What runs **today**:
- Backend: FastAPI, two apps
  - `api.mock:app` for local dev. Deterministic first-person answers.
  - `api.main:app` skeleton. `/chat` returns 503 because real integrations are not implemented.
- Tests: pytest suite against the mock app. An integration test exists for the real app, but it is aspirational.
- Packaging: Python 3.13 compatible. `python-dotenv` for envs.

Planned or referenced, **not implemented in current code**:
- Vector search: Vertex AI Matching Engine.
- Embeddings: text-embedding-004.
- Side store: GCS JSONL gzip, loaded at startup.
- LLM: Gemini Flash.
- Cloud Run deployment and Firebase Hosting.

## Data Flow

**Mock path (active today)**
1. Client POSTs `/chat` with `{"question": "..."}`.
2. The server normalizes third-person mentions of “<PERSONA_NAME>” to first person.
3. Returns a deterministic first-person answer with a dummy citation and usage.

**Intended real path (not implemented yet)**
1. Convert CV or source docs to JSONL chunks.
2. `jobs/pack_and_push.py` validates and writes `chunks-<sha>.jsonl.gz` to GCS.
3. On API startup, load side store, initialize embedding and vector search clients.
4. For each query: embed, vector search top K, light boosting, build strict prompt, call LLM, return structured answer.

## Frontend UX

**Not verified in current pass.** Keep prior v3 outline until the frontend repo is reviewed:
- Welcome message, starter prompts, and a fixed-size page with a scrolling conversation pane.
- Disable input and starters when the backend is down. Poll `/health` and show a short warming-up notice.
- Show TLDR, bullets, and one-line wrap. Keep token usage visible.

fileciteturn0file1

## Security

- API key in `x-api-key`, enforced on `/chat` in the real skeleton app.
- Per-IP rate limits: 10 per minute, 100 per day.
- CORS allowlist limited to localhost and `https://<PROJECT_ID>.web.app`.

## Python 3.13 note

- Run `uvicorn` without extras unless wheels are confirmed.
- `orjson` is optional and not required.
