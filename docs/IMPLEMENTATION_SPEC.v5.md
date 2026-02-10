# IMPLEMENTATION_SPEC v5

## Repos
- Public mono-repo: this zip contains both backend and frontend. The backend is in `api/` and jobs under `jobs/`. The frontend is in `web/`. A private folder for secrets may be referenced during runtime, not committed.

### Current repo tree (trimmed)
```
.
.gitignore
LICENSE
Makefile
README.md
backend/
  Makefile
  README.md
  api/
    __init__.py
    llm.py
    main.py
    mock.py
    retrieval.py
    security.py
    settings.py
    types.py
  config/
    settings.yaml.example
  jobs/
    pack_and_push.py
  pytest.ini
  requirements.txt
  schema/
    chunk.schema.json
  tests/
    conftest.py
    test_integration_real_backend.py
    test_normalize_question_punct.py
    test_persona_voice.py
    test_smoke.py
frontend/
  .firebase/
    hosting.d2ViL291dA.cache
  README.md
  firebase.json
  package-lock.json
  package.json
  web/
    .nvmrc
    components/
      Layout.tsx
    next-env.d.ts
    next.config.mjs
    package.json
    pages/
      _app.tsx
      index.tsx
    postcss.config.mjs
    public/
      android-chrome-192x192.png
      android-chrome-512x512.png
      apple-touch-icon.png
      favicon-16x16.png
      favicon-32x32.png
      favicon.ico
      site.webmanifest
    styles/
      globals.css
    tailwind.config.ts
    tsconfig.json
    utils/
      api.ts
      types.ts
private-template/
  persona/
    assets/
      .gitkeep
    persona.yaml
    starters.json
    vector-seed/
      .gitkeep
  secrets/
    backend.env
    frontend.env
scripts/
  link-private.sh
```

## Environment variables
List of variables discovered in code. Values must be provided via your private folder or environment. Placeholders should not be used in production.
- `API_KEY`: used in backend/api/settings.py
- `CHUNKS_URI`: used in backend/api/settings.py
- `DEPLOYED_INDEX_ID`: used in backend/api/settings.py
- `ENV_DIR`: used in backend/api/settings.py
- `INDEX_ENDPOINT_ID`: used in backend/api/settings.py
- `MAX_INPUT_TOKENS`: used in backend/api/settings.py
- `MAX_OUTPUT_TOKENS`: used in backend/api/settings.py
- `NEXT_PUBLIC_API_URL`: used in frontend/web/components/Layout.tsx, frontend/web/pages/index.tsx, frontend/web/utils/api.ts
- `PERSONA_MAX_WORDS`: used in backend/api/settings.py
- `PERSONA_NAME`: used in backend/api/settings.py
- `PROJECT_ID`: used in backend/api/settings.py
- `REGION`: used in backend/api/settings.py
- `REQ_TIMEOUT_MS`: used in backend/api/settings.py

## Backend API
### Endpoints
- `GET /health` returns `{ "ready": <bool> }`.
- `POST /chat` accepts JSON and returns structured JSON. The real backend currently returns 503 until retrieval and LLM are wired.
### Request schema
- `question`: str
### Response schema
- `answer`: str
- `citations`: List[Citation]
- `usage`: Usage

### Minimal examples
Mock app (if running at port 8000):
```bash
curl -s http://localhost:8000/health | jq .
curl -s -X POST http://localhost:8000/chat -H 'content-type: application/json' -d '{"question":"demo"}' | jq .
```

## Retrieval and LLM pipeline
- `api/retrieval.py`: first-person normalization implemented. Retrieval stubs (`embed_query`, `search_vector_store`, `apply_filters_and_boosting`, `build_context_prompt`) not implemented.
- `api/llm.py`: `build_llm_prompt` returns the strict format. `call_gemini_flash` not implemented.

## Ingestion jobs
- `jobs/pack_and_push.py` validates JSONL against `schema/chunk.schema.json`, splits long texts, writes `chunks-<sha>.jsonl.gz`. Prints a `gs://` URI if a bucket is configured in `config/settings.yaml`. No embedding or upsert yet.

## Frontend behavior
- Frontend folder not found under `web/`.

## Makefile targets (root)
- BACKEND_ENV
- FRONTEND_ENV
- build
- clean
- clean-all
- dev
- fe-install
- install
- mock
- require-private

## Tests
- Python tests under `tests/`:
  - `backend/tests/conftest.py`
  - `backend/tests/test_integration_real_backend.py`
  - `backend/tests/test_normalize_question_punct.py`
  - `backend/tests/test_persona_voice.py`
  - `backend/tests/test_smoke.py`

## Deployment notes
- Cloud Run and Firebase steps exist in docs, but are not verified in code. Keep them as unverified. Ensure CORS allowlist uses your deployed Hosting origin.

## Compatibility and tooling
- Python 3.13 compatible, avoid optional wheels unless verified. `orjson` is optional.

## Provenance
This v5 spec updates v4 to match the merged mono-repo and current code layout. Prior v4 references two repos and unverified frontend details.

References to prior docs: fileciteturn0file3 fileciteturn0file1