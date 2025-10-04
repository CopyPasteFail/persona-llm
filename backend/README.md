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

- **Service account helpers**
  - `make gcp-sa-create` – create `persona-llm@$(PROJECT_ID)`.
  - `make gcp-sa-delete` – remove that service account if you rotate credentials.
  - `make gcp-sa-grant-builder` / `make gcp-sa-grant-runtime` – grant build/runtime IAM roles.
  - `make gcp-sa-key ARGS="--key-file=$HOME/.config/persona-llm/vertex-ai-sa.json"` – issue a JSON key. Store it outside the repos (e.g. `$HOME/.config/persona-llm/vertex-ai-sa.json`, `chmod 600`). Point tooling at it with `GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/persona-llm/vertex-ai-sa.json`.
  - For temporary auth without keys: `gcloud auth application-default login` and `gcloud auth application-default set-quota-project $(PROJECT_ID)`.

## Environment variables
Provide these through your shell or a private folder loader. Do not commit secrets.

Common placeholders:
- `X_API_KEY` for real app requests.
- `BUCKET_NAME` / `CHUNKS_PATH` to locate the packaged JSONL side store (full GCS URI is derived at runtime).
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
The repository includes several layers of automated tests to validate both the mock and real backend behavior, as well as the normalization logic for persona references:

- **Smoke tests** (`test_smoke.py`)  
  Basic checks against the mock API endpoints. They verify that `/health` responds as ready and that `/chat` returns an answer, citations, and token usage in the expected contract:contentReference[oaicite:0]{index=0}.

- **Persona voice tests** (`test_persona_voice.py`)  
  Ensure that the system correctly normalizes references to the persona name into first-person phrasing. These cover edge cases like possessives, bare name substitutions, curly/straight apostrophes, and strings that must not be changed (emails, handles, paths, etc.):contentReference[oaicite:1]{index=1}.

- **Question punctuation normalization tests** (`test_normalize_question_punct.py`)  
  Focus on punctuation handling and string transformations for consistent first-person responses, with exhaustive parameterization across variants of the persona’s name:contentReference[oaicite:2]{index=2}.

- **Integration tests (real backend)** (`test_integration_real_backend.py`)  
  Run against a locally running backend (`uvicorn api.main:app`) with real GCP credentials. They check that `/chat` produces valid responses, includes the expected structure (`answer`, `citations`, `usage`), and returns first-person answers containing pronouns like *I*, *my*, or *me*:contentReference[oaicite:3]{index=3}.
  Integration tests for real mode will fail until that path is implemented.

- **Environment setup for tests** (`conftest.py`)  
  Provides default environment variables so tests can run consistently without requiring manual configuration. These cover persona name, project and region identifiers, index endpoints, tokens, and API keys:contentReference[oaicite:4]{index=4}.

Together, these tests ensure that both the mock and real backends return well-structured responses, and that persona normalization logic behaves correctly under a variety of input forms.

## Deployment
- Cloud Run and related steps exist in the docs but are not verified in code.
- Configure CORS to allow your Hosting origin.
- Set rate limits and API key on the real app.
