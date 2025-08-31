# persona-llm

Monorepo for the persona demo. Frontend in `frontend`, backend in `backend`.
Persona data and secrets point the backend at a local/private folder using `PRIVATE_DIR`.

## Why not a submodule?
- Submodules expose the private repo URL in `.gitmodules`.
- Workarounds like locally setting the Submodule private-url are clunky, for example VS Code revert/undo won't work.
- CI is simpler if we fetch the private overlay explicitly.
- in IDEs like VS Code and similar, one can still manage tracking 2 repos in one folder, without the coupling of a submodule

## Quick start

### Step 1
1. Clone this public repo.
2. Create a private repo from `private-template/`.
3. Link it or point `PRIVATE_DIR` to its `persona/` folder.

#### Option A: symlink at repo root
```bash
# from repo root
ln -s /abs/path/to/your-private-overlay ./private
echo "PRIVATE_DIR=private/persona" > backend/.env
make dev
```

#### Option B: no symlink, use absolute path
```bash
echo "PRIVATE_DIR=/abs/path/to/your-private-overlay/persona" > backend/.env
make dev
```

`backend` should read persona files from `$PRIVATE_DIR`.

### Step 2

You need the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) (`gcloud`).


If this is your first time using it:

--------- CLI auth (first time) ---------
Google Cloud CLI must be installed.
1) Login to gcloud (user account)
```bash
gcloud auth login
```
2) Select the project
```bash
gcloud config set project omer-persona-llm-frontend
```
3) (Optional but handy for some SDKs) Application-default login for user account
gcloud auth application-default login

--------- If you DO NOT have a GCP project yet (optional) ---------
```bash
gcloud projects create "$PROJECT_ID" --name="Persona LLM"  # needs billing set separately
gcloud beta billing projects link "$PROJECT_ID" --billing-account=YOUR_BILLING_ACCOUNT_ID
```

--------- Firebase CLI (optional) ----------
npm install -g firebase-tools
```bash
firebase login
```
If you need to create a Firebase project (optional):
```bash
firebase projects:create "$PROJECT_ID" --display-name "Persona LLM"
```
If the project already exists, just select it in your local firebase state:
```bash
firebase use "$PROJECT_ID"
```

# --------- enable APIs you need ---------
gcloud services enable aiplatform.googleapis.com storage.googleapis.com

# --------- bucket for data/artifacts ---------
gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}"

# --------- service account (for local/dev + CI) ---------
gcloud iam service-accounts create persona-llm --display-name="Persona LLM"

# Minimal roles for runtime querying + GCS access
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:persona-llm@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:persona-llm@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# For *index creation/management* you (or a CI SA) will also need admin during setup.
# Easiest for now: give the same SA admin. You can tighten later.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:persona-llm@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.admin"

# --------- create a key INSIDE the private dir ---------
gcloud iam service-accounts keys create "$ENV_DIR/key.json" \
  --iam-account "persona-llm@${PROJECT_ID}.iam.gserviceaccount.com"

## Repo layout

- [`frontend/`](./frontend/README.md) — Next.js app, scripts and env vars
- [`backend/`](./backend/README.md) — FastAPI app, env vars, API docs
- `private/` (optional symlink) — points to your private overlay for local dev

## CI without submodule (GitHub Actions example)

```yaml
- uses: actions/checkout@v4

# Fetch the private overlay into ./private using a PAT/Deploy Key stored in secrets
- name: Fetch private overlay
  run: |
    git clone "https://x-access-token:${{ secrets.PRIVATE_REPO_TOKEN }}@github.com/<your-user>/<your-private-repo>.git" private

# Build backend image, copying persona files during build (optional) or mounting at runtime
- name: Build backend
  run: |
    docker build -t persona-backend:ci           --build-arg PRIVATE_DIR=private/persona           -f backend/Dockerfile .
```

Alternative: do not copy persona into the image. Deploy the image and mount `$PRIVATE_DIR` or read from object storage.

## Develop

```bash
make install
make dev
```

Mock mode if available:
```bash
make dev:mock
```

## Run Modes

The root `package.json` forwards scripts to the `web` app via `"workspaces": ["web"]`.

### Mode A: Mock frontend + mock backend (local)
Develop the UI against the mock API at `http://localhost:8080`.

Run the mock backend and frontend pointing to the mock. Choose one:
- Fast start (uses cached build):
  ```bash
  make mock
  ```
- Clean start (force rebuild):
  ```bash
  make clean-all
  make mock
  ```

Terminate with Ctrl+C in the terminal.  
If port 3000 is stuck:
```bash
XXXX  kill-port
```

App: http://localhost:3000

---

### Mode B: Local frontend + Cloud Run backend
Run Next.js locally but call the real Cloud Run API.

1) Ensure secrets/backend.env are set in your private repository


2) Start the dev server. Choose one:
- Fast start (uses cached build):
  ```bash
  make fe-dev:mock
  ```
- Clean start (force rebuild):
  ```bash
  make fe-clean:all
  make fe-dev:mock
  ```

App: http://localhost:3000


---

### Mode C: Production (Firebase Hosting + Cloud Run)
Static export on Firebase Hosting. API served by Cloud Run.

1) Ensure secrets/backend.env and secrets/frontend.env are set in your private repository

2) Build the static export:
```bash
XXXX build
```

3) Optional: preview locally:
```bash
XXXX preview
# serves web/out at http://localhost:4173
```

4) Deploy Hosting:
```bash
make fe-firebase:deploy
```
