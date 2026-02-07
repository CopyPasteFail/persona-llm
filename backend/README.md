# Backend, FastAPI apps (`api/`)

FastAPI service for the persona demo. Integrated mode uses local vector search by default and can fall back to Vertex AI Matching Engine. Mock mode returns deterministic responses and is the default for local development.

## Overview
- Two apps:
  - `api.mock:app` for local development.
  - `api.main:app` for integrated runs with retrieval and LLM wiring.
- `/health` (liveness) and `/ready` (readiness) endpoints exist.

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
- `API_KEY` remains the server secret (JWT signing fallback) and optional header for protected admin endpoints.
- `BUCKET_NAME` selects the active dataset version in GCS via the internal pointer (required unless `DATASET_URI` is set).
- `DATASET_URI` optionally overrides the dataset root (e.g., `gs://bucket` or `file:/abs/path`; file paths must be absolute); when unset, `BUCKET_NAME` is used.
- `VECTOR_BACKEND=local|matching_engine` controls vector search; Matching Engine IDs are required only for `matching_engine`.
- `LLM_BACKEND=vertex|deterministic` controls LLM selection (mock defaults to deterministic; use Vertex with ADC).
- `THINKING_BUDGET_TOKENS` (optional) caps Gemini 2.5 "thinking" tokens for chat responses (the backend uses the `google-genai` SDK for Gemini calls).
- `INCLUDE_THOUGHTS=false|true` controls whether the API returns thought parts (defaults to false).
- `OPS_SECRET` protects `/ops/*` endpoints when `OPS_AUTH=enabled`.
- Project, region, and model identifiers if using Vertex; names are placeholders only.
Legacy note: `make be-pack_and_push` is only needed for older `CHUNKS_PATH` deployments.
See `docs/IMPLEMENTATION_SPEC.md` for the full environment contract and defaults.

## Access keys
### Admin CLI
Access keys live in Firestore collection `access_keys`.
You can view them in GCP console using this [link](https://console.cloud.google.com/firestore/databases/-default-/data/panel).

The access keys can be manage using the admin CLI:
- Create: `make be-create-access-key ARGS="create --label demo --expires-in 7d --print-json"`
- Optional JSON output: add `--print-json` to the command above
- Explicit expiry: `make be-create-access-key ARGS="create --expires-at 2024-12-31T23:59:00Z"`
- Revoke: `make be-create-access-key ARGS="revoke --key-id <doc-id>"`
- Optional revoke metadata: add `--project <PROJECT>` and `--revoked-by you`
Keys are not derived from `API_KEY`.

### Mock auth
For local testing with `api.mock:app`, you can use a lightweight JSON key store with plaintext access keys.
The mock app will use `backend/mock_access_keys.json` if it exists, or you can override with `MOCK_ACCESS_KEYS_PATH`.

Mock access keys come from [mock_access_keys.json](backend/mock_access_keys.json); the access key value is the `key` field.

Example JSON:
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
> The canonical information about the API can be found here:
> [API contract and error semantics](../docs/IMPLEMENTATION_SPEC.md#backend-api)
> [Security context (auth, CORS, rate limits, cookies)](../docs/ARCHITECTURE_OVERVIEW.md#security)

- `GET /health` liveness signal.
- `GET /ready` readiness signal (integrated app only).
- `POST /auth/key-login` issues a bearer token.
- `POST /auth/logout` clears session cookies when enabled.
- `POST /chat` requires auth.
- `GET /ops/vector/status` requires `x-ops-secret` when ops auth is enabled.
- `POST /ops/vector/reload` requires `x-ops-secret` and is rate-limited.

### Curl examples
```bash
curl -s http://localhost:8080/health | jq .

KEY="test-key-123" # from backend/mock_access_keys.json in mock mode
TOKEN=$(curl -s -X POST http://localhost:8080/auth/key-login \
  -H 'content-type: application/json' \
  -d "{\"key\":\"$KEY\"}" | jq -r .access_token)

curl -s -X POST http://localhost:8080/chat \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"question":"demo"}' | jq .
```

## Dataset switching (ops)
Atomic update order:
1) Upload `datasets/vNN/datapoints.jsonl`, `datasets/vNN/chunks.jsonl.gz`, `datasets/vNN/manifest.json`
2) Update `datasets/current.json` to `{ "version": "vNN" }`
3) Call `POST /ops/vector/reload` with `x-ops-secret: $OPS_SECRET` (or restart the service)

Local dev can bypass ops auth with `OPS_AUTH=disabled`.

## CORS
Strict allowlist. Integrated mode allows `http://localhost:3000` and `https://<project-id>.web.app` (set exact host before deploy). Mock mode allows `http://localhost:3000` and `http://127.0.0.1:3000`.

## Rate limits (integrated mode)
Per IP, 10 per minute and 100 per day on `/chat`. `/health` is never limited.

## Tests
See [TESTING.md](../docs/TESTING.md) for the full test catalog, how to run tests, and integration requirements.
