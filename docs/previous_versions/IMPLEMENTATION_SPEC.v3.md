# IMPLEMENTATION_SPEC.v3.md

## Repos
- Private repo (<PERSONA_NAME>-llm-backend): ingestion, backend, config, schema, tests
- Public repo (persona-llm-frontend): frontend only

```
<PERSONA_NAME>-llm-backend/
  api/
    main.py
    retrieval.py
    llm.py
    security.py
    settings.py
    types.py
  jobs/
    pack_and_push.py
  schema/
    chunk.schema.json
  config/
    settings.yaml.example  # commit this
    # settings.yaml        # do not commit
  tests/
    test_smoke.py
  Makefile
  requirements.txt         # Python 3.13 friendly pins
  .env.example             # commit this, copy to .env locally

persona-llm-frontend/
  web/
    pages/
      _app.tsx
      index.tsx
    components/
      Layout.tsx
      ChatForm.tsx
      Answer.tsx
    utils/
      api.ts
      types.ts
    styles/
      globals.css
    .env.local.example     # commit this
    # .env.local           # do not commit
    .env.production        # commit placeholders only
  firebase.json
  .firebaserc
  package.json
  tsconfig.json
  next.config.mjs
  postcss.config.mjs
  tailwind.config.ts
  .gitignore
```

## Env Vars (private repo only at runtime)
```env
PROJECT_ID=<string>
REGION=<region>
INDEX_ENDPOINT_ID=<resource path>
DEPLOYED_INDEX_ID=<string>
CHUNKS_URI=gs://bucket/chunks-<sha>.jsonl.gz
API_KEY=<secret>

MAX_INPUT_TOKENS=3000
MAX_OUTPUT_TOKENS=180
REQ_TIMEOUT_MS=20000
```

## Data Schema
Each JSONL record:
```json
{
  "id": "cv:2024:nexyte:devops-engineer:001",
  "text": "Context: Employer=Nexyte, Role=DevOps Engineer, Years=2023-2025...",
  "metadata": {
    "employer": "Nexyte",
    "roles": ["Founder","Product Manager"],
    "role_primary": "Product Manager",
    "year": 2024,
    "start_year": 2023,
    "end_year": 2025,
    "tech": ["kubernetes","ansible"],
    "type": "achievement",
    "section": "experience"
  }
}
```
Validation rules:
- Require keys: id, text, metadata
- Require in metadata: employer, year, tech, section, type
- Role handling: either role (string) or both roles (array) and role_primary (string)
- type is one of achievement or experience

## Ingestion (jobs/pack_and_push.py)
- Input: data/cv_chunks.jsonl by default. You must generate this file from your source (for example .docx CV) using your splitting rules
- Validate against schema/chunk.schema.json
- If text is greater than about 2200 characters, split by sentence boundary. Never cut mid sentence
- Assign deterministic IDs if missing
- Embed with text-embedding-004 in batches
- Upsert to Vertex AI Matching Engine
- Write side store chunks-<sha>.jsonl.gz with id, text, metadata only
- Upload to GCS and print the final CHUNKS_URI

Makefile target:
```make
ingest:
	python jobs/pack_and_push.py --settings config/settings.yaml --schema schema/chunk.schema.json --input data/cv_chunks.jsonl
```

config/settings.yaml example:
```yaml
project_id: YOUR_PROJECT
region: europe-west1
bucket: <PERSONA_NAME>-llm-side-store
index_endpoint_id: projects/XXX/locations/europe-west1/indexEndpoints/NNN
deployed_index_id: <PERSONA_NAME>-llm-deployed
```

## Backend API (FastAPI)
Security:
- Require API key header x-api-key
- Per IP rate limits: 10 per minute and 100 per day on /chat
- Exempt /health from rate limits

Startup and readiness:
- On startup, download CHUNKS_URI, gunzip, build in memory map
- Init embedding client
- Only then set READY flag
- /health returns 200 only when READY is true

Retrieval pipeline:
1. Embed query with text-embedding-004
2. Search Vertex Vector Search top K 8 cosine
3. Apply filters if present: role or roles, year, tech
4. Mild boosting for type=achievement and tie break by newer year when scores are close
5. Build strict prompt from selected chunks
6. Enforce MAX_INPUT_TOKENS by dropping lowest score chunks last
7. Call Gemini Flash with max_output_tokens = MAX_OUTPUT_TOKENS
8. Return JSON with answer, citations, and usage

Error handling:
- 400 invalid input or too large context
- 429 rate limited
- 503 generic failures
- Log request id, timings, retrieved ids, and token counts. Never log raw user questions

CORS:
- Allow only http://localhost:3000 and https://<project-id>.web.app

## Frontend (Next.js static plus Firebase Hosting)
- Static export build
- Welcome message and 3 starter prompts
- On load: probe /health with 800 ms timeout. If not ready, show "Warming up..." and poll each second until ready
- Submit handler calls /chat with x-api-key if provided
- Render TLDR, bullets, wrap. Show token usage
- Env usage:
  - .env.local for local dev. Not committed. Can point to local backend or Cloud Run URL during development
  - .env.production for production static export. Commit placeholders only. No secrets
  - .env.local.example committed to teach others how to configure local env

## Monitoring and cost
- Cloud Logging: request id, latency, retrieved ids, token counts
- Cloud Monitoring: counters for requests, errors, and latency
- Cost guardrail: GCP Budget alerts only

## Tests
- tests/test_smoke.py runs a few fixed queries. Assert HTTP code and basic keyword checks

## Deployment
One time GCP:
- Create private GCS bucket for side store
- Create Matching Engine index and endpoint
- Create Cloud Run service account with roles aiplatform.user, aiplatform.viewer and Matching Engine read permissions, and storage.objectViewer on the bucket
- Set a project budget with alerts at 90 percent and 100 percent

First ingestion:
- You must generate data/cv_chunks.jsonl from your .docx using ChatGPT and commit to private repo
- Run make ingest to embed, upsert, and upload side store. Note printed CHUNKS_URI

Cloud Run deploy:
```bash
gcloud run deploy ask-<PERSONA_NAME>-api --source ./api --region $REGION --service-account $RUNTIME_SA \
  --set-env-vars PROJECT_ID=$PROJECT_ID,REGION=$REGION,INDEX_ENDPOINT_ID=$IE,DEPLOYED_INDEX_ID=$DI,CHUNKS_URI=$URI,API_KEY=$API_KEY,MAX_INPUT_TOKENS=3000,MAX_OUTPUT_TOKENS=180,REQ_TIMEOUT_MS=20000
```

Firebase Hosting:
- Ensure web/.env.production points to the Cloud Run URL
- Build and deploy Hosting

## Python 3.13 compatibility
- Use uvicorn without extras by default. Avoid uvloop and httptools unless confirmed available
- orjson is optional. Add it only when wheels exist for your platform

## .gitignore guidance
- Private repo: ignore .env and .env.*, ignore config/settings.yaml, commit config/settings.yaml.example
- Public repo: ignore web/.env.local, commit web/.env.local.example, commit web/.env.production with placeholders only
