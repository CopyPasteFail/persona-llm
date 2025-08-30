# Backend, FastAPI apps (`api/`)

FastAPI service for the persona demo. Real mode integrates with Vertex AI Matching Engine and Gemini. Mock mode returns deterministic responses and is the default for local development.

## Overview
- Two apps:
  - `api.mock:app` for local development.
  - `api.main:app` skeleton. `/chat` raises not implemented until retrieval and LLM are wired.
- Health endpoint and minimal `/chat` contract exist.

## Prerequisites
- Python 3.13
- `uvicorn`, `fastapi`, and deps from `pyproject.toml` or `requirements.txt`.

## Setup
```bash
# optional: create virtualenv
python3.13 -m venv .venv
source .venv/bin/activate

# install deps
pip install -U pip
pip install -e .  # adjust per your repo
```

## Run
Mock server, default port 8080:
```bash
python -m uvicorn api.mock:app --reload --port 8080
```

Real server, unverified:
```bash
python -m uvicorn api.main:app --reload --port 8000
# /chat will return 503 or raise NotImplemented until retrieval and LLM are wired
```

If a `Makefile` is present, common targets:
```bash
make BACKEND_ENV FRONTEND_ENV build clean clean-all dev fe-install install mock require-private
```

## Environment variables
Provide these through your shell or a private folder loader. Do not commit secrets.

Common placeholders:
- `X_API_KEY` for real app requests.
- `CHUNKS_URI` for the packaged JSONL side store.
- Project, region, and model identifiers if using Vertex, names are placeholders only.

## API
### `GET /health`
- Returns readiness status.

### `POST /chat`
- Request JSON:
```json
{ "question": "your text" }
```
- Response JSON, mock:
```json
{ "answer": "text", "citations": [{"id":"mock:1"}], "usage": {"input_tokens": 0, "output_tokens": 0} }
```

Curl examples:
```bash
curl -s http://localhost:8080/health | jq .
curl -s -X POST http://localhost:8080/chat -H 'content-type: application/json' -d '{"question":"demo"}' | jq .
```

## CORS
Strict allowlist. Real mode allows `http://localhost:3000` and `https://<project-id>.web.app` (set exact host before deploy). Mock mode only allows `http://localhost:3000`.

## Rate limits (real mode)
Per IP, 10 per minute and 100 per day on `/chat`. `/health` is never limited.

## Tests
- Run pytest from repo root if tests are present:
```bash
pytest -q
```
Some tests target the mock app. Integration tests for real mode will fail until that path is implemented.

## Deployment
- Cloud Run and related steps exist in the docs but are not verified in code.
- Configure CORS to allow your Hosting origin.
- Set rate limits and API key on the real app.
