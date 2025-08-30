# persona-llm-backend

FastAPI service for the persona demo. Real mode integrates with Vertex AI Matching Engine and Gemini. Mock mode returns deterministic responses and is the default for local development.

For setup and run steps see **QUICKSTART.md** in this directory.

## API

- `GET /health` readiness
- `POST /chat` accepts and returns JSON per the schema below

### Request JSON (snake_case)
{ "question": "string", "role": "string or null", "year": 2024, "tech": ["kubernetes","terraform"] }

### Response JSON
{ "answer": "string", "citations": [{"id": "string"}], "usage": {"input_tokens": 123, "output_tokens": 120} }

## Ingestion
1. Create `data/cv_chunks.jsonl` from your CV, one JSON object per line, validated by `schema/chunk.schema.json`.
2. Run ingestion:
   ```bash
   make ingest
   ```
   The job will output a `chunks-<sha>.jsonl.gz` and print a `CHUNKS_URI`. Put that value into your env or `config/settings.yaml`.

## Run tests
```bash
make test         # run smoke tests (basic keyword checks)
make test-voice   # run persona voice and normalization tests
```

## Deploy to Cloud Run
Fill placeholders with your values.
```bash
gcloud run deploy ask-persona-api   --source ./api   --region $REGION   --service-account $RUNTIME_SA   --set-env-vars PROJECT_ID=$PROJECT_ID,REGION=$REGION,INDEX_ENDPOINT_ID=$INDEX_ENDPOINT_ID,DEPLOYED_INDEX_ID=$DEPLOYED_INDEX_ID,CHUNKS_URI=$CHUNKS_URI,API_KEY=$API_KEY,MAX_INPUT_TOKENS=3000,MAX_OUTPUT_TOKENS=180,REQ_TIMEOUT_MS=20000
```

## Security notes
- The API requires `x-api-key`. Do not put real secrets in public repos.
- Keep `config/settings.yaml` and `.env` out of git. Only commit the `.example` files.

## CORS
Strict allowlist. Real mode allows `http://localhost:3000` and `https://<project-id>.web.app` (set exact host before deploy). Mock mode only allows `http://localhost:3000`.

## Rate limits (real mode)
Per IP, 10 per minute and 100 per day on `/chat`. `/health` is never limited.

## Logging
Structured keys only: `request_id`, timings, retrieved ids, token counts. Never log raw user inputs or secrets.

## Repo structure
```
api/            # FastAPI app
jobs/           # pack_and_push.py ingestion
schema/         # chunk.schema.json
config/         # settings.yaml.example
tests/          # tests
Makefile
requirements.txt
```