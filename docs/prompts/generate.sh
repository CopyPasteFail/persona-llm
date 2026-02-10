# Creates omer-cv-persona-rag/ with both repos and zips it to omer-cv-persona-rag.zip
set -euo pipefail
ROOT="omer-cv-persona-rag"
BACK="$ROOT/omer-llm-backend"
FRONT="$ROOT/persona-llm-frontend"
mkdir -p "$BACK/api" "$BACK/tests" "$FRONT/web/pages" "$FRONT/web/components" "$FRONT/web/utils" "$FRONT/web/styles"

# ----- backend -----
cat >"$BACK/Makefile" <<'EOF'
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help install run run-mock test check envs clean

help:
	@echo "Targets:"
	@echo "  install   - create venv and install deps"
	@echo "  run       - start real API skeleton on :8000 (requires envs)"
	@echo "  run-mock  - start mock API on :8080"
	@echo "  test      - run pytest smoke tests against mock app"
	@echo "  check     - quick import check"
	@echo "  envs      - print required env variables"
	@echo "  clean     - remove caches and venv"

$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	touch $(VENV)/bin/activate

install: $(VENV)/bin/activate

run: install
	$(PY) -m uvicorn api.main:app --host 0.0.0.0 --port 8000

run-mock: install
	$(PY) -m uvicorn api.mock:app --host 0.0.0.0 --port 8080

test: install
	$(PY) -m pytest -q

check:
	$(PY) -c "import fastapi, uvicorn, pydantic, httpx, yaml; print('ok')"

envs:
	@echo "Required (real mode): PROJECT_ID, REGION, INDEX_ENDPOINT_ID, DEPLOYED_INDEX_ID, CHUNKS_URI, API_KEY"
	@echo "Optional: MAX_INPUT_TOKENS (default 3000), MAX_OUTPUT_TOKENS (default 180), REQ_TIMEOUT_MS (default 20000)"

clean:
	rm -rf __pycache__ .pytest_cache $(VENV)
EOF

cat >"$BACK/README.md" <<'EOF'
# omer-llm-backend

FastAPI service for the persona demo. Real mode integrates with Vertex AI Matching Engine and Gemini. Mock mode returns deterministic responses and is the default for local development.

For setup and run steps see **QUICKSTART.md** in this directory.

## API

- `GET /health` readiness
- `POST /chat` accepts and returns JSON per the schema below

### Request JSON (snake_case)
{ "question": "string", "role": "string or null", "year": 2024, "tech": ["kubernetes","terraform"] }

### Response JSON
{ "answer": "string", "citations": [{"id": "string"}], "usage": {"input_tokens": 123, "output_tokens": 120} }

## CORS
Strict allowlist. Real mode allows `http://localhost:3000` and `https://<project-id>.web.app` (set exact host before deploy). Mock mode only allows `http://localhost:3000`.

## Rate limits (real mode)
Per IP, 10 per minute and 100 per day on `/chat`. `/health` is never limited.

## Logging
Structured keys only: `request_id`, timings, retrieved ids, token counts. Never log raw user inputs or secrets.
EOF

cat >"$BACK/QUICKSTART.md" <<'EOF'
# QUICKSTART

Single source of truth for setup and running both repos.

## Prerequisites
- Python 3.13
- Node 18+
- Make

## Backend
cd omer-llm-backend
make install
make run-mock  # localhost:8080

# Real mode skeleton (requires .env with real values)
cp .env.example .env
make run  # localhost:8000

## Frontend
cd persona-llm-frontend
npm install
npm run dev:mock  # uses http://localhost:8080

## Tests
cd omer-llm-backend && make test

## Docs
- Backend details: omer-llm-backend/README.md
- Frontend details: persona-llm-frontend/README.md
EOF

cat >"$BACK/requirements.txt" <<'EOF'
fastapi==0.116.1
starlette==0.47.3
uvicorn==0.30.1
pydantic>=2.7.1,<3
python-dotenv==1.0.1
pytest==8.2.2
httpx==0.27.0
PyYAML==6.0.1
EOF

cat >"$BACK/.env.example" <<'EOF'
PROJECT_ID=your-project-id
REGION=europe-west1
INDEX_ENDPOINT_ID=projects/...
DEPLOYED_INDEX_ID=your-deployed-id
CHUNKS_URI=gs://bucket/chunks-<sha>.jsonl.gz
API_KEY=changeme
MAX_INPUT_TOKENS=3000
MAX_OUTPUT_TOKENS=180
REQ_TIMEOUT_MS=20000
EOF

cat >"$BACK/.gitignore" <<'EOF'
.env
.env.*
__pycache__/
.pytest_cache/
config/settings.yaml
EOF

cat >"$BACK/api/__init__.py" <<'EOF'
# pkg marker
EOF

cat >"$BACK/api/settings.py" <<'EOF'
from pydantic import BaseModel, Field, ValidationError, field_validator
from dotenv import load_dotenv
import os

PLACEHOLDERS = {
    "changeme","your-project-id","your-index-id","your-endpoint-id","projects/...","your-deployed-id","gs://bucket/chunks-<sha>.jsonl.gz",
}

class Settings(BaseModel):
    PROJECT_ID: str = Field(...)
    REGION: str = Field(...)
    INDEX_ENDPOINT_ID: str = Field(...)
    DEPLOYED_INDEX_ID: str = Field(...)
    CHUNKS_URI: str = Field(...)
    API_KEY: str = Field(...)
    MAX_INPUT_TOKENS: int = Field(3000, ge=1, le=10000)
    MAX_OUTPUT_TOKENS: int = Field(180, ge=1, le=2000)
    REQ_TIMEOUT_MS: int = Field(20000, ge=1000, le=120000)

    @field_validator("PROJECT_ID","REGION","INDEX_ENDPOINT_ID","DEPLOYED_INDEX_ID","CHUNKS_URI","API_KEY", mode="before")
    @classmethod
    def no_placeholders(cls, v: str):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("missing")
        lv = v.strip().lower()
        if lv in {p.lower() for p in PLACEHOLDERS}:
            raise ValueError("placeholder not allowed")
        return v.strip()

def load_settings() -> "Settings":
    load_dotenv(override=False)
    try:
        return Settings(
            PROJECT_ID=os.getenv("PROJECT_ID"),
            REGION=os.getenv("REGION"),
            INDEX_ENDPOINT_ID=os.getenv("INDEX_ENDPOINT_ID"),
            DEPLOYED_INDEX_ID=os.getenv("DEPLOYED_INDEX_ID"),
            CHUNKS_URI=os.getenv("CHUNKS_URI"),
            API_KEY=os.getenv("API_KEY"),
            MAX_INPUT_TOKENS=int(os.getenv("MAX_INPUT_TOKENS") or 3000),
            MAX_OUTPUT_TOKENS=int(os.getenv("MAX_OUTPUT_TOKENS") or 180),
            REQ_TIMEOUT_MS=int(os.getenv("REQ_TIMEOUT_MS") or 20000),
        )
    except ValidationError as e:
        fields = [err["loc"][0] for err in e.errors()]
        raise RuntimeError(f"Invalid settings. Fix env vars: {sorted(set(fields))}")

settings = load_settings()
EOF

cat >"$BACK/api/types.py" <<'EOF'
from pydantic import BaseModel, Field
from typing import List, Optional

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    role: Optional[str] = None
    year: Optional[int] = None
    tech: Optional[List[str]] = None

class Citation(BaseModel):
    id: str

class Usage(BaseModel):
    input_tokens: int
    output_tokens: int

class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation]
    usage: Usage
EOF

cat >"$BACK/api/security.py" <<'EOF'
from fastapi import Header, HTTPException, Request
from typing import Optional
from collections import deque
import time
from .settings import settings

PER_MINUTE = 10
PER_DAY = 100
WINDOW_MIN = 60.0
WINDOW_DAY = 86400.0

_hits_min = {}
_hits_day = {}

async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not x_api_key or x_api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="forbidden")

async def check_rate_limit_dependency(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    dq1 = _hits_min.setdefault(ip, deque())
    dq2 = _hits_day.setdefault(ip, deque())
    while dq1 and now - dq1[0] > WINDOW_MIN:
        dq1.popleft()
    while dq2 and now - dq2[0] > WINDOW_DAY:
        dq2.popleft()
    if len(dq1) >= PER_MINUTE or len(dq2) >= PER_DAY:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    dq1.append(now)
    dq2.append(now)
EOF

cat >"$BACK/api/retrieval.py" <<'EOF'
# Real mode stubs. Keep pure and side-effect free.
def embed_query(question: str):
    raise NotImplementedError("embed_query not implemented")

def search_vector_store(embedding, top_k: int = 8):
    raise NotImplementedError("search_vector_store not implemented")

def apply_filters_and_boosting(candidates, role=None, year=None, tech=None):
    raise NotImplementedError("apply_filters_and_boosting not implemented")

def build_context_prompt(question: str, selected):
    raise NotImplementedError("build_context_prompt not implemented")
EOF

cat >"$BACK/api/llm.py" <<'EOF'
def build_llm_prompt(context: str):
    raise NotImplementedError("build_llm_prompt not implemented")

def call_gemini_flash(prompt: str, max_output_tokens: int):
    raise NotImplementedError("call_gemini_flash not implemented")
EOF

cat >"$BACK/api/main.py" <<'EOF'
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .types import ChatRequest, ChatResponse, Citation, Usage
from .security import verify_api_key, check_rate_limit_dependency
from .settings import settings
from . import retrieval, llm
import logging, time, uuid

READY = False
app = FastAPI(title="Persona LLM API", version="1.0.0")

origins = [
    "http://localhost:3000",
    f"https://{settings.PROJECT_ID}.web.app" if settings.PROJECT_ID else "https://placeholder.web.app",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET","POST"],
    allow_headers=["*"],
)

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

@app.on_event("startup")
def on_startup():
    global READY
    READY = True

@app.get("/health")
def health():
    if not READY:
        raise HTTPException(status_code=503, detail="not ready")
    return {"ready": True}

@app.post("/chat", dependencies=[Depends(verify_api_key), Depends(check_rate_limit_dependency)])
def chat(req: ChatRequest) -> ChatResponse:
    if not READY:
        raise HTTPException(status_code=503, detail="not ready")
    request_id = str(uuid.uuid4())
    t0 = time.time()
    try:
        emb = retrieval.embed_query(req.question)
        cands = retrieval.search_vector_store(emb, top_k=8)
        selected = retrieval.apply_filters_and_boosting(cands, role=req.role, year=req.year, tech=req.tech)
        context = retrieval.build_context_prompt(req.question, selected)
        prompt = llm.build_llm_prompt(context)
        _ = llm.call_gemini_flash(prompt, max_output_tokens=settings.MAX_OUTPUT_TOKENS)
        raise NotImplementedError("Real mode not implemented. Use mock backend locally.")
    except NotImplementedError as e:
        logger.info({"request_id": request_id, "elapsed_ms": int((time.time()-t0)*1000)})
        raise HTTPException(status_code=503, detail=str(e))
EOF

cat >"$BACK/api/mock.py" <<'EOF'
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .types import ChatRequest, ChatResponse, Citation, Usage

app = FastAPI(title="Persona LLM API (mock)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET","POST"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ready": True}

def _tldr(text: str) -> str:
    words = text.strip().split()
    if not words:
        return "No question provided."
    k = 16 if len(words) >= 16 else len(words)
    return " ".join(words[:k])

def _bullets(role, year, tech):
    bullets = []
    if role:
        bullets.append(f"Role filter: {role}")
    if year is not None:
        bullets.append(f"Year filter: {year}")
    if tech:
        bullets.append("Tech filter: " + ", ".join(tech[:6]))
    stock = [
        "Grounded in local mock data.",
        "Deterministic output for repeatable testing.",
        "No external calls in mock mode."
    ]
    for s in stock:
        if len(bullets) >= 5: break
        bullets.append(s)
    return bullets[:5]

@app.post("/chat")
def chat(req: ChatRequest) -> ChatResponse:
    q = req.question or ""
    tldr = _tldr(q)
    bullets = _bullets(req.role, req.year, req.tech)
    wrap = "Summary: short, grounded, and deterministic."
    answer = f"TLDR: {tldr}\n- " + "\n- ".join(bullets) + f"\nWrap: {wrap}"
    usage = Usage(input_tokens=max(1, len(q)//4), output_tokens=120)
    citations = [Citation(id="mock:1")]
    return ChatResponse(answer=answer, citations=citations, usage=usage)
EOF

cat >"$BACK/tests/test_smoke.py" <<'EOF'
import pytest
from httpx import AsyncClient
from api.mock import app as mock_app

@pytest.mark.asyncio
async def test_health_ready():
    async with AsyncClient(app=mock_app, base_url="http://test") as ac:
        r = await ac.get("/health")
        assert r.status_code == 200
        assert r.json().get("ready") is True

@pytest.mark.asyncio
async def test_chat_basic():
    async with AsyncClient(app=mock_app, base_url="http://test") as ac:
        payload = {"question": "Tell me about my Kubernetes experience and Ansible work.", "role": None, "year": 2024, "tech": ["kubernetes","ansible"]}
        r = await ac.post("/chat", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert "TLDR:" in data["answer"]
        assert any("Tech filter" in line for line in data["answer"].splitlines())
        assert data["citations"] and data["citations"][0]["id"] == "mock:1"
        assert data["usage"]["output_tokens"] == 120
EOF

# ----- frontend -----
cat >"$FRONT/README.md" <<'EOF'
# persona-llm-frontend

Public Next.js + Tailwind frontend for the persona demo. It adapts snake_case API fields to camelCase in the client.

For setup and run steps see the backend QUICKSTART.md.
EOF

cat >"$FRONT/package.json" <<'EOF'
{
  "name": "persona-llm-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "next dev -p 3000",
    "dev:mock": "sh -c 'NEXT_PUBLIC_API_URL=http://localhost:8080 next dev -p 3000'",
    "build": "next build && next export",
    "start": "next start -p 3000"
  },
  "dependencies": {
    "next": "14.2.5",
    "react": "18.3.1",
    "react-dom": "18.3.1"
  },
  "devDependencies": {
    "autoprefixer": "10.4.19",
    "postcss": "8.4.39",
    "tailwindcss": "3.4.7",
    "typescript": "5.4.5"
  }
}
EOF

cat >"$FRONT/next.config.mjs" <<'EOF'
/** @type {import('next').NextConfig} */
const nextConfig = { output: 'export', reactStrictMode: true };
export default nextConfig;
EOF

cat >"$FRONT/postcss.config.mjs" <<'EOF'
export default { plugins: { tailwindcss: {}, autoprefixer: {} } }
EOF

cat >"$FRONT/tailwind.config.ts" <<'EOF'
export default { content: ['./web/**/*.{ts,tsx,js,jsx,html}'], theme: { extend: {} }, plugins: [] }
EOF

cat >"$FRONT/tsconfig.json" <<'EOF'
{
  "compilerOptions": {
    "target": "ES2021",
    "lib": ["dom", "es2021"],
    "jsx": "preserve",
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "strict": true,
    "skipLibCheck": true,
    "baseUrl": ".",
    "paths": { "@/*": ["web/*"] }
  },
  "include": ["web"]
}
EOF

cat >"$FRONT/web/pages/_app.tsx" <<'EOF'
import type { AppProps } from 'next/app'
import '@/styles/globals.css'
export default function App({ Component, pageProps }: AppProps) { return <Component {...pageProps} /> }
EOF

cat >"$FRONT/web/pages/index.tsx" <<'EOF'
import { useEffect, useState } from 'react'
import Layout from '@/components/Layout'
import ChatForm from '@/components/ChatForm'
import Answer from '@/components/Answer'
import { getHealth } from '@/utils/api'

export default function Home() {
  const [ready, setReady] = useState(false)
  useEffect(() => {
    let active = true
    const probe = async () => {
      const ok = await getHealth(800)
      if (!active) return
      if (ok) setReady(true); else { setReady(false); setTimeout(probe, 1000) }
    }
    probe()
    return () => { active = false }
  }, [])
  return (
    <Layout>
      {!ready && <div className="mb-4 rounded border p-3 text-sm">Warming up...</div>}
      <h1 className="text-2xl font-semibold mb-4">Ask the Persona</h1>
      <ChatForm disabled={!ready} />
      <Answer />
    </Layout>
  )
}
EOF

cat >"$FRONT/web/components/Layout.tsx" <<'EOF'
import { PropsWithChildren } from 'react'
export default function Layout({ children }: PropsWithChildren) {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-3xl mx-auto px-4 py-6">
        <header className="mb-6"><div className="text-lg font-medium">Persona Demo</div></header>
        <main>{children}</main>
        <footer className="mt-10 text-xs opacity-70">Built for local-first, deterministic testing.</footer>
      </div>
    </div>
  )
}
EOF

cat >"$FRONT/web/components/ChatForm.tsx" <<'EOF'
import { useState } from 'react'
import { postChat } from '@/utils/api'
import { ChatRequest } from '@/utils/types'

export default function ChatForm({ disabled }: { disabled?: boolean }) {
  const [question, setQuestion] = useState('')
  const [role, setRole] = useState('')
  const [year, setYear] = useState('')
  const [tech, setTech] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    const payload: ChatRequest = {
      question,
      role: role || null,
      year: year ? Number(year) : null,
      tech: tech ? tech.split(',').map(s => s.trim()).filter(Boolean) : null,
    }
    await postChat(payload)
    setLoading(false)
  }
  return (
    <form onSubmit={submit} className="space-y-3 mb-6">
      <input className="w-full rounded bg-zinc-900 border border-zinc-800 px-3 py-2" placeholder="Your question" value={question} onChange={e => setQuestion(e.target.value)} disabled={disabled || loading} required />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <input className="rounded bg-zinc-900 border border-zinc-800 px-3 py-2" placeholder="role (optional)" value={role} onChange={e => setRole(e.target.value)} disabled={disabled || loading} />
        <input className="rounded bg-zinc-900 border border-zinc-800 px-3 py-2" placeholder="year (optional)" value={year} onChange={e => setYear(e.target.value)} disabled={disabled || loading} />
        <input className="rounded bg-zinc-900 border border-zinc-800 px-3 py-2" placeholder="tech comma separated (optional)" value={tech} onChange={e => setTech(e.target.value)} disabled={disabled || loading} />
      </div>
      <div className="flex items-center gap-2">
        <button type="submit" className="rounded bg-emerald-600 px-4 py-2 disabled:opacity-50" disabled={disabled || loading}>{loading ? 'Sending…' : 'Ask'}</button>
        {disabled && <span className="text-sm opacity-70">Backend not ready</span>}
      </div>
    </form>
  )
}
EOF

cat >"$FRONT/web/components/Answer.tsx" <<'EOF'
import { useEffect, useState } from 'react'
import { lastAnswer$ } from '@/utils/api'
import { ChatResponse } from '@/utils/types'

export default function Answer() {
  const [data, setData] = useState<ChatResponse | null>(null)
  useEffect(() => {
    const unsub = lastAnswer$.subscribe(setData)
    return () => unsub()
  }, [])
  if (!data) return null
  const lines = data.answer.split('\n')
  const tldr = lines.find(l => l.startsWith('TLDR:')) || ''
  const bullets = lines.filter(l => l.startsWith('- ')).map(l => l.slice(2))
  const wrap = lines.find(l => l.startsWith('Wrap:')) || ''
  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="font-medium mb-2">{tldr.replace(/^TLDR:\s*/, '')}</div>
      <ul className="list-disc list-inside mb-2 space-y-1">
        {bullets.map((b, i) => <li key={i}>{b}</li>)}
      </ul>
      <div className="text-sm opacity-80 mb-2">{wrap.replace(/^Wrap:\s*/, '')}</div>
      {data.usage && <div className="text-xs opacity-60">tokens in: {data.usage.inputTokens} · out: {data.usage.outputTokens}</div>}
    </div>
  )
}
EOF

cat >"$FRONT/web/utils/api.ts" <<'EOF'
import { ChatRequest, ChatResponse } from './types'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'

function toCamel(o: any): any {
  if (Array.isArray(o)) return o.map(toCamel)
  if (o && typeof o === 'object') {
    return Object.fromEntries(Object.entries(o).map(([k, v]) => [
      k.replace(/_([a-z])/g, (_, c) => c.toUpperCase()),
      toCamel(v),
    ]))
  }
  return o
}

const state = { last: null as ChatResponse | null }
type Subscriber = (d: ChatResponse | null) => void
const subs = new Set<Subscriber>()
export const lastAnswer$ = {
  subscribe(fn: Subscriber) { subs.add(fn); fn(state.last); return () => subs.delete(fn) }
}

export async function getHealth(timeoutMs = 800): Promise<boolean> {
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const r = await fetch(`${API_URL}/health`, { signal: ctrl.signal })
    return r.ok
  } catch {
    return false
  } finally {
    clearTimeout(t)
  }
}

export async function postChat(payload: ChatRequest): Promise<ChatResponse> {
  const r = await fetch(`${API_URL}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const data = toCamel(await r.json()) as ChatResponse
  state.last = data
  subs.forEach(fn => fn(state.last))
  return data
}
EOF

cat >"$FRONT/web/utils/types.ts" <<'EOF'
export interface ChatRequest {
  question: string
  role: string | null
  year: number | null
  tech: string[] | null
}
export interface Citation { id: string }
export interface Usage { inputTokens: number; outputTokens: number }
export interface ChatResponse { answer: string; citations: Citation[]; usage: Usage }
EOF

cat >"$FRONT/web/styles/globals.css" <<'EOF'
@tailwind base;
@tailwind components;
@tailwind utilities;
:root { color-scheme: dark; }
EOF

cat >"$FRONT/web/.env.local.example" <<'EOF'
NEXT_PUBLIC_API_URL=http://localhost:8080
# NEXT_PUBLIC_STARTER_PROMPTS=["Show Kubernetes highlights","Summarize 2024 work"]
EOF

cat >"$FRONT/web/.env.production" <<'EOF'
NEXT_PUBLIC_API_URL=https://<project-id>.web.app/api-proxy
EOF

cat >"$FRONT/.gitignore" <<'EOF'
node_modules/
.next/
out/
web/.env.local
EOF

# Zip
zip -rq "omer-cv-persona-rag.zip" "$ROOT"
echo "Created omer-cv-persona-rag.zip"
