# ARCHITECTURE_OVERVIEW.v3.md

## Goal
A reusable public showcase where people can query a "persona" LLM representing a human. Answers are grounded in a provided dataset, for example a CV, using Vertex AI Vector Search. Priorities: low cost, low ops, transparent design.

## Stack
- Repos:
  - Private backend and ingestion: **<PERSONA_NAME>-llm-backend**
  - Public frontend showcase: **persona-llm-frontend**
- Frontend: Next.js static export, hosted on Firebase Hosting
- Backend: FastAPI on Cloud Run
- Vector search: Vertex AI Matching Engine (Tree-AH, cosine)
- Embeddings: text-embedding-004 (768d)
- Side store: JSONL gzip in GCS bucket, loaded at startup
- LLM: Gemini 2.0 Flash with strict grounding and short answer style
- Auth: API key header and per IP rate limiting (10 per minute, 100 per day)
- Cold start: min instances 0. Frontend shows "Warming up..." until /health is ready
- CORS: strict allowlist (localhost:3000 and <project>.web.app)
- Monitoring: Cloud Logging and Cloud Monitoring metrics. Budget alerts only

## Data Flow
1. Manual step: Convert CV .docx to JSONL chunks using ChatGPT with max ~450 tokens per chunk. Store in the private repo only
2. Ingestion job (pack_and_push.py): validate, embed, upsert to Vector Search, gzip JSONL, upload to GCS
3. Cloud Run API loads chunks-<sha>.jsonl.gz from GCS at startup
4. Query flow: user question goes to backend, embed query, Vector Search top K 8, apply mild boosting and filters, call Gemini Flash with strict grounding, return structured answer

## Frontend UX
- Welcome message and starter prompts
- Question input posts to /chat
- Render TLDR, 3 to 5 bullets, and a one line wrap
- Copy to clipboard, basic dark mode
- Token usage is shown
- Show "Warming up..." while health is false

## Security
- Private repo holds all env vars and identifiers: PROJECT_ID, REGION, INDEX_ENDPOINT_ID, DEPLOYED_INDEX_ID, CHUNKS_URI, API_KEY
- Public repo contains frontend only. Never commit real secrets. Anything NEXT_PUBLIC_* is visible to end users

## Python 3.13 note
- Use uvicorn without the [standard] extras to avoid uvloop or httptools on 3.13 unless you confirm wheels exist
- orjson is optional. Start with stdlib json and add orjson later if wheels are available
