# Frontend, Next.js app (`web/`)

Next.js + Tailwind frontend for the persona LLM. It adapts snake_case API fields to camelCase in the client.

## Overview
- Framework: Next.js
- Location: `web/`
- Talks to the backend `/chat` endpoint. In mock mode the backend returns deterministic answers.

## Prerequisites
- Node 20.x
- npm 10.x or later

## Install
```bash
npm install -w web
```

## Commands

`frontend/package.json` forwards scripts to the `web` app via `"workspaces": ["web"]`.

```bash
# dev against mock backend
npm run dev:mock -w web

# dev against real backend (requires real API on :8000 or configured URL)
npm run dev -w web

# build and preview
npm run build -w web
npm run preview -w web

# clean and kill leftover ports (if provided in package.json)
npm run clean -w web
npm run kill-port -w web
```
From `web/` folder you can run the same scripts without `-w web`.

## Environment variables

Notes:
- Client visible variables must start with `NEXT_PUBLIC_`.
- Do not put secrets here. Use the private folder or backend settings.

## Behavior
- The input area is disabled when the backend is not reachable.
- Starter prompts do not insert when API is down.
- Conversation has its own scroll area so the page layout stays fixed.
- Errors from fetch or 503 responses should show a visible signal in the UI.

## Dev backends
- Mock app default port: 8080.
- Real backend path is unverified until retrieval and LLM are wired.


---

## Firebase config files

- **`firebase.json`**  
  Hosting configuration. Tells Firebase to serve the static export from `web/out`:contentReference[oaicite:0]{index=0}.

---

## Notes
- CORS: the backend allowlists `http://localhost:3000` and your Firebase Hosting URL.
- `.env.local` is ignored. `.env.local.example` documents variables. `.env.production` has placeholders for static export.
- Do not run `next export` directly — static export is enabled by `output: 'export'` in `web/next.config.mjs`.
"

