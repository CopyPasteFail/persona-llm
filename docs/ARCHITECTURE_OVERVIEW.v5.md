# ARCHITECTURE_OVERVIEW v5

## Goal
A reusable public showcase where people can query a "persona" LLM representing a human. Answers are grounded in a provided dataset, for example a CV, using Vertex AI Vector Search. Priorities: low cost, low ops, transparent design.

## Stack
- **Backend**: FastAPI with two apps.
  - `api.mock:app` for local dev, deterministic answers.
  - `api.main:app` skeleton where `/chat` returns 503 until real retrieval and LLM are wired.
- **Frontend**: Next.js app in `web/` with starter prompts and a fixed layout. Verify disabled states when the backend is down, and independent scroll for the conversation pane.
- **Jobs**: `jobs/pack_and_push.py` to validate and package JSONL chunks.
- **Tests**: pytest suite focused on the mock app. One integration test expects a real backend and will fail until implemented.

## Data flow
**Mock path (active today)**
1. Client POSTs `/chat` with `{ "question": "..." }`.
2. Question is normalized to first person.
3. Service returns a deterministic answer with a dummy citation and usage.

**Intended real path (not implemented)**
1. Load side store from `CHUNKS_URI`.
2. Embed query, search vector index, apply filters and boosting, and build context.
3. Call LLM with strict prompt builder and return structured answer.

## Security
- `x-api-key` required on real app paths. Rate limits per IP: 10 per minute, 100 per day.
- CORS allowlist: localhost and your Hosting origin built from `PROJECT_ID`.

## Environments and variables
Environment variables detected in code:
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

## Deployment
- Cloud Run and Firebase Hosting instructions are kept, not verified in code.
- Real mode will not work until retrieval, vector search, and LLM calls are implemented.

## Repo layout (trimmed)
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

References to prior docs: fileciteturn0file0 fileciteturn0file1