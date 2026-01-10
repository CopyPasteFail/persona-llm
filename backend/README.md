# Backend, FastAPI apps (`api/`)

FastAPI service for the persona demo. Real mode integrates with Vertex AI Matching Engine and Gemini. Mock mode returns deterministic responses and is the default for local development.

## Overview
- Two apps:
  - `api.mock:app` for local development.
  - `api.main:app` skeleton. `/chat` raises not implemented until retrieval and LLM are wired.
- `/health` endpoint and minimal `/chat` contract exist.

## Prerequisites
- Python 3.13
- `uvicorn`, `fastapi`, and deps from `pyproject.toml` or `requirements.txt`.

### Installing Dependencies on Debian/Ubuntu

#### Set desired Python version
```bash
PY_VER=3.13
```

#### Update packages and install Python
```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python${PY_VER} python${PY_VER}-venv python${PY_VER}-dev
```

Verify:
```bash
python${PY_VER} --version
```

#### Install `pip`
```bash
sudo apt install -y python${PY_VER}-distutils
```

Verify:
```bash
python${PY_VER} -m pip --version
```
## Development Setup


## Commands
```bash
make help
```

## Environment variables
Provide these through your shell or a private folder loader. Do not commit secrets.

Common placeholders:
- Access keys live in Firestore collection `access_keys`; manage them with the admin CLI:
  - Create: `python scripts/create_access_key.py create --label demo --expires-in 7d [--print-json]`
  - Explicit expiry: `python scripts/create_access_key.py create --expires-at 2024-12-31T23:59:00Z`
  - Revoke: `python scripts/create_access_key.py revoke --key-id <doc-id> [--project <PROJECT>] [--revoked-by you]`
  Keys are not derived from `API_KEY`.
- `API_KEY` remains the server secret (JWT signing fallback) and optional header for protected admin endpoints.
- `BUCKET_NAME` / `CHUNKS_PATH` to locate the packaged JSONL side store (full GCS URI is derived at runtime).
- Project, region, and model identifiers if using Vertex, names are placeholders only.

## Mock auth
For local testing with `api.mock:app`, you can use a lightweight JSON key store with plaintext access keys.
The mock app will use `backend/mock_access_keys.json` if it exists, or you can override with `MOCK_ACCESS_KEYS_PATH`.

Example JSON (see `backend/mock_access_keys.json`):
```json
{
  "keys": [
    {
      "id": "demo-1",
      "label": "local dev",
      "key": "test-key-123",
      "expires_at": "2030-01-01T00:00:00Z",
      "revoked": false
    }
  ]
}
```

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
See `docs/TESTING.md` for the full test catalog, how to run tests, and integration requirements.
