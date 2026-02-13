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

- `npm run dev`: start the Next.js dev server on port 3000.
- `npm run dev:local`: dev server with `NEXT_PUBLIC_API_URL` pointing at `http://localhost:8080`.
- `npm run dev:clean`: clear `.next`, `out`, and `.turbo`, then launch the dev server.
- `npm run dev:local:clean`: clean artifacts before starting the local-connected dev server.
- `npm run build`: create the production build with `next build`.
- `npm run preview`: serve the static export from `web/out` on port 4173 (requires a prior `npm run build`).
- `npm run clean`: remove `.next`, `out`, and `.turbo` artifacts.
- `npm run clean:all`: run `clean` and also wipe `node_modules` and `.firebase` in the frontend workspace.
- `npm run kill-port`: free up port 3000 if a dev server is stuck.
- `npm run firebase`: run arbitrary Firebase CLI commands in the `web` workspace.
- `npm run firebase:version`: print the Firebase CLI version through the workspace wrapper.
- `npm run firebase:login`: authenticate the Firebase CLI via the workspace wrapper.
- `npm run firebase:use`: load secrets from `$PRIVATE_DIR/secrets/{common,frontend}.env`, require `PROJECT_ID`, then set the active Firebase project.
- `npm run firebase:create`: same environment bootstrap as `firebase:use`, then create a Firebase project named after `PROJECT_ID`.
- `npm run firebase:deploy`: load secrets, run `npm run build`, and deploy hosting/config to the `PROJECT_ID` Firebase project.
- `npm run firebase:hosting:disable`: load secrets and disable Firebase Hosting for the configured `PROJECT_ID`.


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
- Integrated backend is `api.main:app` and runs retrieval plus LLM generation.

---

## Firebase config files

- **`firebase.json`**  
  Hosting configuration. Tells Firebase to serve the static export from `web/out`.

### Authentication for deploys
- **Personal account (default):** if you haven’t already, run `gcloud auth login` and `gcloud auth application-default login` so both the Cloud SDK and Application Default Credentials use your user identity. The Firebase CLI automatically reuses those tokens, so a separate `firebase login` is only needed if you explicitly want to switch accounts.

- **Service account / automation:** export `GOOGLE_APPLICATION_CREDENTIALS=$PRIVATE_DIR/secrets/key.json` (or another ADC-compatible key path) before executing `npm run firebase:deploy`. This matches the service-account workflow described in the root README.

---

## Notes
- `.env.local` is ignored. `.env.local.example` documents variables. `.env.production` has placeholders for static export.
- Do not run `next export` directly, static export is enabled by `output: 'export'` in `web/next.config.mjs`.
