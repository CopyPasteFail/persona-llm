# persona-llm-frontend

Public Next.js + Tailwind frontend for the persona demo. It adapts snake_case API fields to camelCase in the client.

## Prerequisites
- Node.js 20.x (LTS). Use nvm if possible.
- npm ≥10.x

## Development Setup

### Node.js version
- If you are using nvm, run:
  ```bash
  nvm install 20
  ```
- An `.nvmrc` file is included. Running:
  ```bash
  nvm use
  ```
  will select the correct version.

- Check your versions:
  ```bash
  node -v   # v20.x.x
  npm -v    # >=10.x
  ```

All commands below are run from the repo root unless noted otherwise.

### Creating a Firebase project
If you have not created a Firebase project yet:

1. Go to the [Firebase Console](https://console.firebase.google.com/).
2. Click **Add project**.
3. Enter a project name (for example `persona-llm-frontend`).  
   Firebase will generate a **Project ID** (lowercase with dashes). You can edit it at this stage, but once created it cannot be changed.
4. Disable Google Analytics (optional).
5. Finish creation.

## Install
Using npm workspaces, everything works from the root:
```bash
npm install
```

## Environment
- Local development uses `web/.env.local` (not committed).
- Example file: `web/.env.local.example`.
- Production export uses `web/.env.production` (committed with placeholders only).

---

## Run Modes

The root `package.json` forwards scripts to the `web` app via `"workspaces": ["web"]`.

### Mode A: Mock frontend + mock backend (local)
Develop the UI against the mock API at `http://localhost:8080`.

1) Start your mock backend (in the backend repo):
```bash
make mock
```

2) Run the frontend pointing to the mock. Choose one:
- Fast start (uses cached build):
  ```bash
  npm run dev:mock
  ```
- Clean start (force rebuild):
  ```bash
  npm run dev:mock:clean
  ```

Terminate with Ctrl+C in the terminal.  
If port 3000 is stuck:
```bash
npm run kill-port
```

App: http://localhost:3000

---

### Mode B: Local frontend + Cloud Run backend
Run Next.js locally but call the real Cloud Run API.

1) Set your Cloud Run URL in `.env.local`:
```bash
echo "NEXT_PUBLIC_API_URL=https://YOUR-CLOUD-RUN-URL" > web/.env.local
```

2) Start the dev server. Choose one:
- Fast start (uses cached build):
  ```bash
  npm run dev
  ```
- Clean start (force rebuild):
  ```bash
  npm run dev:clean
  ```

App: http://localhost:3000

Tip: keep `.env.local` pointed at Cloud Run as your default, and use `npm run dev:mock` when you want to hit the mock backend.

---

### Mode C: Production (Firebase Hosting + Cloud Run)
Static export on Firebase Hosting. API served by Cloud Run.

1) Ensure `web/.env.production` points to your Cloud Run URL (placeholders only, no secrets):
```
NEXT_PUBLIC_API_URL=https://YOUR-CLOUD-RUN-URL
```

2) Build the static export:
```bash
npm run build
```

3) Optional: preview locally:
```bash
npm run preview
# serves web/out at http://localhost:4173
```

4) Deploy Hosting:
```bash
npm run firebase:deploy
```

---

## Root-level commands
Run these from the repo root:

- `npm run dev` start frontend in dev mode with cached build
- `npm run dev:clean` clean then start frontend in dev mode
- `npm run dev:mock` start frontend in dev mode against mock backend
- `npm run dev:mock:clean` clean then start frontend in mock mode
- `npm run build` build and export to `web/out`
- `npm run preview` serve `web/out` at http://localhost:4173
- `npm run clean` remove build artifacts (`.next`, `out`, `.turbo`)
- `npm run kill-port` free port 3000 if stuck
- `npm run firebase:login` log in to Firebase
- `npm run firebase:deploy` build then deploy to Hosting

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
