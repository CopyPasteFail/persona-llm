# persona-llm

Monorepo for the persona demo. Frontend in `frontend`, backend in `backend`.
Persona data and secrets point the backend at a local/private folder using `PRIVATE_DIR`.

## Why not a submodule?
- Submodules expose the private repo URL in `.gitmodules`.
- Workarounds like locally setting the Submodule private-url are clunky, for example VS Code revert/undo won't work.
- CI is simpler if we fetch the private overlay explicitly.
- in IDEs like VS Code and similar, one can still manage tracking 2 repos in one folder, without the coupling of a submodule

## Quick start

### Step 1. Clone and set up your private overlay

1. Clone this repo.
2. Copy `private-template/` into a new private repo or folder.  
   This holds your secrets and persona data and must not be committed.
3. Link it or point `PRIVATE_DIR` to its location.

#### Option A: symlink the private overlay into ./private
```bash
ln -s /abs/path/to/your-private-overlay ./private
```

#### Option B: sticky override (gitignored)
```bash
echo "/abs/path/to/your-private-overlay" > .privatedir
```

#### Option C: ad-hoc override
```bash
PRIVATE_DIR=/abs/path/to/your-private-overlay make run
```

> **After this step:**  
> Check the prerequisites and installation instructions in:
> - `backend/README.md` (Python version, venv, installing deps)  
> - `frontend/README.md` (Node version, npm install, etc.)

> **Node version auto-switching**  
> The repo pins a version of Node in `.nvmrc`.
> For bash shells, append this helper to `~/.bashrc`:
> ```bash
> cat <<'EOF' >> ~/.bashrc
> load-nvmrc() {
>   local nvmrc="$PWD/.nvmrc"
>   if [ -f "$nvmrc" ]; then
>     nvm use --silent >/dev/null 2>&1 || nvm install
>   fi
> }
> export PROMPT_COMMAND="load-nvmrc${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
> load-nvmrc
> EOF
> ```
> Restart the shell (or run `source ~/.bashrc`) and bash will run `nvm use` whenever you `cd` into this repo.


At this stage you can locally run the mock backend and frontend, see [here](#mode-a-mock-frontend--mock-backend-local).

## Preparing GCP and Firebase Environments

### Required environment variables

Inside your private overlay pointed to by `PRIVATE_DIR`, you must include these files with the following variables.
`PROJECT_ID` and `BUCKET_NAME` will be used to either create new projects/bucket with the given IDs or to reference existing ones.  
**Note:**  
- By default, creating a project in Firebase will also create it in GCP.  
- This app uses a single `PROJECT_ID` for both Firebase and GCP so everything stays in sync.

- `secrets/common.env`  
  - `PROJECT_ID` — shared project identifier for both Firebase and GCP resources. It must follow Google’s naming conventions *(lowercase letters, digits, and hyphens, 6–30 characters, starting with a letter, and not ending with a hyphen)*.

- `secrets/frontend.env`  
  - Frontend-only overrides such as `NEXT_PUBLIC_API_URL`.

- `secrets/backend.env`  
  - `REGION` — the GCP region where resources (like Cloud Run and buckets) will be created, for example `europe-west1`.
  - `BUCKET_NAME` — the name of the GCS bucket used for storage, for example `my-project-persona`. Do not prefix with `gs://`.

### Phase 1. Install CLI tools

#### Firebase CLI:

```bash
make fe-firebase:login
```

#### Google Cloud CLI

You can either run the commands below or check the [official instructions](https://cloud.google.com/sdk/docs/install#linux).

Commands for Linux:

```bash
(
  cd ~
  curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz
  tar -xf google-cloud-cli-linux-x86_64.tar.gz
  rm google-cloud-cli-linux-x86_64.tar.gz
  ./google-cloud-sdk/install.sh
)
```

When running `install.sh` you will be prompted:
- *Do you want to help improve the Google Cloud CLI (y/N)?* → type **n**  
- *Modify profile to update your $PATH and enable shell command completion? (Y/n)* → type **y**  
- *Enter a path to an rc file to update, or leave blank to use [/home/YOUR_USERNAME/.bashrc]:* → press **Enter**

Finally, update your shell environment:

```bash
source ~/.bashrc
```

Verify installation:
```bash
gcloud --version
```

### Phase 2. Authenticate once

```bash
gcloud auth login
gcloud auth application-default login
```

### Phase 3. Choose your project workflow

- [Create a brand-new project](#workflow-new-project)
- [Reuse an existing GCP project (no Firebase yet)](#workflow-existing-gcp)
- [Reuse an existing Firebase project](#workflow-existing-firebase)

#### Workflow A: brand-new project {#workflow-new-project}
```bash
make gcp-create-project
make gcp-set-project
```

#### Workflow B: existing GCP project (no Firebase yet) {#workflow-existing-gcp}
```bash
make gcp-set-project
make gcp-enable-firebase
```

#### Workflow C: existing Firebase project {#workflow-existing-firebase}
```bash
make gcp-set-project
```

### Phase 4. Billing Account Verification {#billing-verification}

Linking a billing account is **mandatory** when using services such as Cloud Run and Vertex AI. Without billing linked, the rest of the provisioning steps will fail.  

To check whether billing is linked, run:
```bash
make gcp-check-billing
```
If the command prints nothing or `False`, you need to link a billing account.
If you don't know your **billing account ID**, you can set and/or create one by follwing the instruction in the [Billing Account](#billing-account) section.

Otherwise set `BILLING_ACCOUNT_ID` and run:
```bash
BILLING_ACCOUNT_ID=YOUR_BILLING_ACCOUNT_ID make gcp-link-billing
```
Re-run `make gcp-set-project` afterwards.

### Phase 5.A Enable services

Enable APIs
```bash
make gcp-enable-apis
```

### Phase 5.B Create resources

Create a bucket for data/artifacts (run once per project)
```bash
make gcp-create-bucket
```

Enable Firebase features (safe to rerun; it’s a no-op if the project is already linked):
```bash
make gcp-enable-firebase
```

Create Firestore database (run once per project)
```bash
make gcp-firestore-init
```


### Phase 6. Choose your deployment identity

Most teams can deploy using their personal Google account. Make sure your user has enough IAM permissions (Project Owner or the specific Vertex AI / Cloud Run / Storage / Firebase roles) and then authenticate as follows:

- `gcloud auth login` — signs the Cloud SDK in as you, so `gcloud`, `gsutil`, and similar CLI commands run with your user credentials.
- `gcloud auth application-default login` — writes Application Default Credentials (ADC) so local scripts and libraries (like `google-cloud-storage`) run with the same user identity.

Run both commands if you deploy via the CLI and run helper scripts locally. If you instead use a dedicated service account, skip these and authenticate with that identity.

With those credentials in place you can run `make be-pack_and_push`, `gcloud run deploy`, and `npm run firebase:deploy` without introducing any new secrets.

### Phase 7. Package persona chunks (side-store artifact)

Generate the `CHUNKS_PATH` side-store artifact before embedding or deploying so the backend has a verified data bundle to load.

```bash
make be-pack_and_push
```

This job reads `private/persona/data/chunks.jsonl`, validates and splits the records, then writes `chunks-<sha>.jsonl.gz` plus `chunks-<sha>.jsonl.gz.manifest.json`. Both files are uploaded to `gs://$BUCKET_NAME/`, and the manifest captures the artifact URI, record count, byte size, and SHA-256 checksum. Record the printed artifact name in `private/secrets/backend.env` as `CHUNKS_PATH`; the manifest travels with the artifact so you (or automation) can verify integrity later.

### Phase 8. Service account (optional)

If you prefer a non-human identity (for CI pipelines or shared deploy scripts), create a service account and grant it temporary builder roles:

```bash
make gcp-sa-create
make gcp-sa-grant-builder   # Vertex AI admin, Storage write, Firebase admin + hosting
make gcp-sa-grant-runtime   # Vertex AI user, Storage read
# Optional clean-up
make gcp-sa-revoke-builder
make gcp-sa-revoke-runtime
# Delete the service account if you rotate identities
make gcp-sa-delete
```

Run the revoke commands after deployment to drop elevated privileges before returning the service account to runtime-only access.

For automation that needs Application Default Credentials:

- Issue a key file (store it outside both repos, for example `$HOME/.config/persona-llm/vertex-ai-sa.json`):
  ```bash
  mkdir -p $HOME/.config/persona-llm
  make gcp-sa-key KEY_FILE=$HOME/.config/persona-llm/vertex-ai-sa.json
  chmod 600 $HOME/.config/persona-llm/vertex-ai-sa.json
  ```

- Add the path to your private env (e.g. `private/secrets/backend.env`):
  ```
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/persona-llm/vertex-ai-sa.json
  ```
- Optional per-session override:
  ```bash
  export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/persona-llm/vertex-ai-sa.json"
  ```

- Alternatively, skip keys and rely on user credentials:
  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project "$PROJECT_ID"
  ```

### Phase 9. Provision Vertex AI Vector Search (one-time)

Set up a Matching Engine index before you embed and upsert persona chunks.

1. Create the index (Tree-AH, dot product, dimensions derived from `DATAPOINTS_DIMENSIONS`—3,072 for `gemini-embedding-001`, 768 for the `text-embedding-00x` family):
   ```bash
   make gcp-index-create
   ```
   - The target generates the JSON config on the fly (Tree-AH with `leafNodeEmbeddingCount=1000`, `leafNodesToSearchPercent=7`, `approximateNeighborsCount=100`, and dot-product distance; embeddings should be unit-normalized for cosine behaviour) and calls `gcloud ai indexes create` with your `PROJECT_ID`/`REGION`.
   - Capture the printed resource name (`projects/<project>/locations/<region>/indexes/<INDEX_ID>`) and export `INDEX_ID` for later steps.
   - Re-running the target creates an additional index (the API is not idempotent); run `make gcp-index-list` to inspect existing indexes.

2. Create an index endpoint:
   ```bash
   make gcp-index-endpoint-create
   ```
   - Capture the endpoint resource (`projects/<project>/locations/<region>/indexEndpoints/<INDEX_ENDPOINT_ID>`) and export `INDEX_ENDPOINT_ID` (store just `<INDEX_ENDPOINT_ID>`; the tooling derives the full path).
   - The command creates a new endpoint each time; delete unused endpoints via `gcloud ai index-endpoints delete` if you re-run it.

3. Deploy the index (requires `INDEX_ID`, `INDEX_ENDPOINT_ID`, and `DEPLOYED_INDEX_ID`; set them in your environment or `backend.env`):
   ```bash
   make gcp-index-deploy
   ```
   - Optional: override the configured deployment name with `make gcp-index-deploy DEPLOYED_INDEX_ID=persona_deployment`.
   - Vertex AI requires the deployed ID to start with a letter and use only letters, numbers, or underscores (e.g. `persona_deployment`).
   - Replica counts are controlled by `ME_MIN_REPLICAS`/`ME_MAX_REPLICAS` in `private/secrets/backend.env` (default 1/1). Increase `ME_MAX_REPLICAS` if you want autoscaling headroom.
   - Deployment can take minutes. While it is provisioning, `gcloud ai index-endpoints describe projects/$PROJECT_ID/locations/$REGION/indexEndpoints/$INDEX_ENDPOINT_ID --region=$REGION --format='yaml(deployedIndexes)'` returns `null`; once the operation finishes it prints the deployed index details (ID, replicas, synced index ID).
   - **Cost to keep in mind:** Vertex AI Vector Search serving bills by node hour (SKU `DAB1-0292-8330`). A single `e2-standard-16` replica in `europe-west3` is roughly `$0.6165/hr` (~`$443.88` per month) *even when idle*, and the charge scales linearly with each additional replica you keep Ready.
   Billing is fractional: the deployed duration is rounded up to the nearest 30-second increment and counted toward node-hours (e.g., 45 minutes = 0.75 node-hours). See the pricing table for your region: https://cloud.google.com/vertex-ai/pricing?hl=en and also consult the pricing here: https://cloud.google.com/vertex-ai/generative-ai/pricing.
   - **Cost control tips:** undeploy the index when you are not actively testing/serving to stop charges instantly, choose the smallest machine type that meets latency goals, and keep `ME_MIN_REPLICAS`/`ME_MAX_REPLICAS` at the minimum that satisfies your QPS requirements so you do not pay for unused capacity.

4. Generate embedding datapoints for the persona chunks:
   ```bash
   make be-build_datapoints
   ```
   - Produces the path configured in `DATAPOINTS_FILE` (set in `private/secrets/backend.env`) with `datapointId` + `featureVector` rows ready for Matching Engine.
   - Optional overrides live in the same env file, e.g. set `DATAPOINTS_MODEL=gemini-embedding-001` (3,072‑dim) or `text-embedding-005` (768‑dim), align `DATAPOINTS_DIMENSIONS` with the chosen model (≤3,072 for Gemini, ≤768 for the text-embedding family), bump `DATAPOINTS_BATCH_SIZE=32`, set `DATAPOINTS_MAX_CHARS=1800`, or enable `DATAPOINTS_GZIP=1` to adjust behavior without command-line flags.
   - Each datapoint emits both `id` and `datapointId`; Vertex’s batch rebuild requires `id`, while our runtime retrieval still reads `datapointId`, so the job keeps them identical.
   - To sanity-check the datapoint writer helpers after any changes, run the focused unit tests:
     ```bash
     make be-test_build_datapoints
     ```

5. Batch-update the index (rebuild from the new datapoints file):
   ```bash
   make gcp-index-upsert
   ```
   - Requires `DATAPOINTS_FILE` to be configured in `private/secrets/backend.env` (for example `$PRIVATE_DIR/persona/data/datapoints.jsonl`).
   - The target stages the datapoints as `datapoints.json` in a timestamped folder under `gs://$BUCKET_NAME/matching-engine/` (it will decompress `.jsonl.gz` automatically) and invokes `gcloud ai indexes update` with that folder URI as `contentsDeltaUri`.
   - `gcloud ai indexes update` refreshes the Vertex AI Vector Search (Matching Engine) index by ingesting the staged datapoints JSON from GCS and rebuilding the index contents.
   - If you prefer manual control, run the `gsutil cp` + `gcloud ai indexes update` commands yourself; the generated metadata snippet lives at `/tmp/` and can be inspected/edited before re-running.
   - The update request runs asynchronously. Capture the printed operation ID (for example `projects/.../operations/<ID>`), then poll it using the Makefile helper so your configured env vars are reused:
     ```bash
     make gcp-index-op-describe OPERATION_ID=<ID>
     make gcp-index-op-done OPERATION_ID=<ID>
     make gcp-index-op-errors OPERATION_ID=<ID>
     ```
     `gcp-index-op-describe` prints YAML with timestamps and any error info so you can track progress. `gcp-index-op-done` emits `True` once the update finishes.
     `gcp-index-op-errors` emits a JSON summary (without the huge `featureVector` arrays) so you can quickly see which datapoints failed validation.
     Once the operation finishes without error, confirm the index picked up the new datapoints by checking the `updateTime`:
     ```bash
     make gcp-index-update-time
     ```

Record `INDEX_ENDPOINT_ID` (bare endpoint ID), `INDEX_ID`, and `DEPLOYED_INDEX_ID` in `private/secrets/backend.env`. Re-run the upsert target whenever persona data changes.

#### Vector Search Roles and Flows

See [docs/VECTOR_SEARCH.md](docs/VECTOR_SEARCH.md) for roles, workflows, and a diagram.

## Repo layout

- [`frontend/`](./frontend/README.md) — Next.js app, scripts and env vars
- [`backend/`](./backend/README.md) — FastAPI app, env vars, API docs
- `private/` — points to your private overlay for local dev

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

> Tip: confirm your shell auto-loads Node 20 (see the setup note above) so these commands use the supported runtime.

Mock mode if available:
```bash
make dev:mock
```

## Run Modes

The root `package.json` forwards scripts to the `web` app via `"workspaces": ["web"]`.

### Mode A: Mock frontend + mock backend (local)
Develop the UI against the mock API.

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
make fe-kill-port
```

If port 8080 is stuck:
```bash
PID=$(lsof -ti :8080) && [ -n "$PID" ] && kill -9 $PID
```

Open the UI at `http://localhost:8080`
Information about the hardcoded (or customization of) access keys can be found [here](backend/README.md#mock-auth).

---

### Mode B: Local frontend + Cloud Run backend
Run Next.js locally but call the real Cloud Run API.

1) Ensure secrets/backend.env are set in your private repository

2) Deploy backend to Cloud Run (pick one):
- Real backend:
  ```bash
  make gcp-cloud-run-deploy
  ```
- Mock backend:
  ```bash
  make gcp-cloud-run-deploy-mock
  ```

Access keys for the real backend live in Firestore. You can view them in the console [here](https://console.cloud.google.com/firestore/databases/-default-/data/panel).

For managing the keys, see [admin CLI access key management](backend/README.md#admin-cli--access-keys).

3) Verify the deployed API (point at the Cloud Run URL from deploy):
  ```bash
  PYTEST_ADDOPTS="-s" make be-test-int
  ```

4) Start the dev server. Choose one:
- Fast start (uses cached build):
  ```bash
  make fe-dev
  ```
- Clean start (force rebuild):
  ```bash
  make fe-clean:all
  make fe-dev
  ```

App: http://localhost:3000

> To target your Cloud Run service, set `NEXT_PUBLIC_API_URL` in `private/secrets/frontend.env` (or export it in the shell) to the HTTPS URL returned by `gcloud run deploy` before running the dev command.

---

### Mode C: Production (Firebase Hosting + Cloud Run)
Static export on Firebase Hosting. API served by Cloud Run.

> Note: ensure `secrets/backend.env` and `secrets/frontend.env` are set in your private repo.

1) Create Artifact Registry once:
   ```bash
   make gcp-create-artifact-registry
   ```

2) Build and push the backend image (pick one path only):
   - Option A — Cloud Build (no local Docker):
     ```bash
     make gcp-cloud-build
     ```
   - Option B — Local Docker + push (Docker needed):
     - One-time per machine: auth to Artifact Registry
       ```bash
       make gcp-auth-registry
       ```
     - Per build: build locally and push
       ```bash
       make be-docker-build
       make gcp-push-backend
       ```

3) Deploy to Cloud Run and note the service URL:
   ```bash
   make gcp-cloud-run-deploy
   ```
   Set `NEXT_PUBLIC_API_URL` to that URL for the frontend. Drop `--allow-unauthenticated` in the Makefile if you want a private service.

4) Build the static export:
   ```bash
   make fe-build
   ```

5) Optional: preview locally:
   ```bash
   make fe-preview
   ```

6) Deploy Hosting:
   ```bash
   make fe-firebase:deploy
   ```

7) Manage Access keys
  For managing the keys, see [admin CLI](backend/README.md#admin-cli).

## Undeploy / Teardown

### Cloud Run
Delete the deployed backend service:
```bash
make gcp-cloud-run-delete
```

Delete the deployed mock backend service:
```bash
make gcp-cloud-run-delete-mock
```

### Firebase Hosting
Disable Hosting for the configured Firebase project:
```bash
make fe-firebase:hosting:disable
```

## Appendix

### Billing Account

To link using the Google Cloud Console:

1. Open the [Billing page](https://console.cloud.google.com/billing).
2. If you already have a billing account:
   - Click **My projects** in the left menu.
   - Find your project `<PROJECT_ID>`.
   - If it shows “No billing account”, click **Link a billing account** and select the account you want.
3. If you don’t have a billing account yet:
   - In the **Billing** page, click **Add billing account**.
   - Follow the steps to create a billing profile and payment method.
   - Once created, go back to **My projects**, find your project, and link it to the new billing account.
