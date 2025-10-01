# Frontend, Next.js app (`web/`)

Next.js + Tailwind frontend for the persona LLM. It adapts snake_case API fields to camelCase in the client.

## Overview
- Framework: Next.js
- Location: `web/`
- Talks to the backend `/chat` endpoint. In mock mode the backend returns deterministic answers.

## Prerequisites
- Node 20.x
- npm 10.x or later

### Installing Dependencies on Debian/Ubuntu
```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc  # or ~/.zshrc depending on your shell
nvm install 20
```

## Development Setup
```bash
npm install -w web
```

## Commands

`frontend/package.json` forwards scripts to the `web` app via `"workspaces": ["web"]`.


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
  Hosting configuration. Tells Firebase to serve the static export from `web/out`.

### Authentication for deploys
- **Personal account (default):** if you haven’t already, run `gcloud auth login` and `gcloud auth application-default login` so both the Cloud SDK and Application Default Credentials use your user identity. The Firebase CLI automatically reuses those tokens, so a separate `firebase login` is only needed if you explicitly want to switch accounts.

- **Service account / automation:** export `GOOGLE_APPLICATION_CREDENTIALS=$PRIVATE_DIR/secrets/key.json` (or another ADC-compatible key path) before executing `npm run firebase:deploy`. This matches the service-account workflow described in the root README.

---

## Notes
- CORS: the backend allowlists `http://localhost:3000` and your Firebase Hosting URL.
- `.env.local` is ignored. `.env.local.example` documents variables. `.env.production` has placeholders for static export.
- Do not run `next export` directly — static export is enabled by `output: 'export'` in `web/next.config.mjs`.
