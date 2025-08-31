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

1. Clone this public repo.
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

At this stage you can locally run the mock backend and frontend, see [here](#mode-a-mock-frontend--mock-backend-local).

## Preparing GCP and Firebase Environments

### Required environment variables

Inside your private overlay pointed to by `PRIVATE_DIR`, you must include these files with the following variables.
`FIREBASE_PROJECT_ID`, `PROJECT_ID`, and `BUCKET_NAME` will be used to either create new projects/bucket with the given IDs or to reference existing ones.  
**Note:**  
- By default, creating a project in Firebase will also create it in GCP.  
- Only choose a different project ID if you want to manage a different GCP project separately from your Firebase project.

- `secrets/frontend.env`  
  - `FIREBASE_PROJECT_ID` — the Firebase project ID used for hosting and deployment.
    Typically this is the same as `PROJECT_ID`. It must follow Google’s project ID naming conventions.
    *(lowercase letters, digits, and hyphens, 6–30 characters, starting with a letter, and not ending with a hyphen)*

- `secrets/backend.env`  
  - `PROJECT_ID` — the GCP project ID to use for backend resources. In most setups this matches the backend `FIREBASE_PROJECT_ID`.
  - `REGION` — the GCP region where resources (like Cloud Run and buckets) will be created, for example `europe-west1`.
  - `BUCKET_NAME` — the name of the GCS bucket used for storage, for example `my-project-persona`. Do not prefix with `gs://`.

### Step 1. Install CLI tools

#### Firebase CLI:

```bash
make firebase:login
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

### Step 2. First-time setup

Authenticate with Google Cloud:
```bash
gcloud auth login
```

(Optional) Create a **new Firebase project** if you haven’t already, or skip if you want to reuse an existing one
```bash
make fe-firebase:create
```
If you see an error like this:
```
firebase > firebase projects:create YOUR_PROJECT_NAME
✔ What would you like to call your project? (defaults to your project ID) YOUR_PROJECT_NAME
✖ Creating Google Cloud Platform project
Error: Failed to create project because there is already a project with ID YOUR_PROJECT_NAME. Please try again with a unique project ID.
```
It means the project ID you chose is already taken globally. You’ll need to select a different, unique project ID.

Select the project to be used by Firebase:
```bash
make fe-firebase:use
```

(Optional) Create a **new GCP project** if you haven’t already, or skip if you want to reuse an existing one.

```bash
make gcp-create-project
```

Set your active project:

```bash
make gcp-set-project
```

If you see a warning like:

```
WARNING: Project <PROJECT_ID> does not have billing linked.
```

you need to link a billing account. This is **mandatory** for services such as Cloud Run and Vertex AI. Without billing linked, the rest of the provisioning steps will fail.  
See [Billing account instructions](#billing-account) below.

After linking, re-run:
```bash
make gcp-set-project
```
to verify billing is now attached.

### Step 4. Enable services and create resources

Enable APIs
```bash
gcloud services enable aiplatform.googleapis.com storage.googleapis.com
```

Create a bucket for data/artifacts
```bash
make gcp-create-bucket
```

### Step 5. Service account and keys

```bash
make gcp-sa-create
make gcp-sa-grant-builder
make gcp-sa-key
```

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

## Appendix

### Billing account

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
