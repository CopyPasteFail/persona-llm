# Testing

This repo currently has backend-focused tests. The frontend does not have a dedicated test suite yet.

> All commands assume you run them from the repo root. If you run them from `backend/`, drop the `be-` prefix (for example, `make test-smoke` instead of `make be-test-smoke`).

## Quick start

```bash
make be-install
```

## Test Suites

### Smoke Test
```bash
make be-test-smoke
```

### Core mock-safe backend suite
```bash
make be-test-core
```

### Integration tests

> To skip integration tests in a broader run, use `-m "not integration"`

#### Real backend integration
Requirements:
- Real backend running (for example `uvicorn api.main:app`)
- `NEXT_PUBLIC_API_URL` pointing to the running backend
- `ACCESS_KEY_PLAINTEXT` set to a valid access key
  ```bash
  export ACCESS_KEY_PLAINTEXT="your-access-key"
  ```

Commands:
```bash
make be-test-int
```

#### Deployed CORS integration
Requirements:
- Deployed backend URL set via `NEXT_PUBLIC_API_URL`
- Frontend Hosting origin via `FRONTEND_ORIGIN` or `PROJECT_ID` (derived as `https://<project-id>.web.app`)

Command:
```bash
make be-test-cors-deploy
```

#### Live vector search integration
Requirements:
- Access to the private Vertex AI Matching Engine endpoint
- `DATAPOINTS_FILE` configured so `scripts.emit_test_embedding` can derive a test vector

Command:
```bash
make be-test-vector-live
```

The live test also honors:
- `RUN_VERTEX_SEARCH_TEST=1`
- `VERTEX_TEST_EMBEDDING` (comma-separated floats)
- `VERTEX_TEST_TOP_K` (optional, default 4)

### Frontend tests
No frontend test suite is wired up yet.

## Test catalog

### Backend mock API tests
- `backend/tests/test_smoke.py`  
  Basic checks against the mock API endpoints. They verify that `/health` responds as ready and that `/chat` returns an answer, citations, and token usage in the expected contract.
  Command:
  ```bash
  make be-test-smoke
  ```

- `backend/tests/test_auth_key_login.py`  
  Exercises access-key login behavior in the mock app. It covers invalid, expired, and revoked keys, confirms success responses include a bearer token and expiry, and checks rate limiting plus cookie-session settings.
  Command:
  ```bash
  make be-test-auth-key-login
  ```

- `backend/tests/test_security_session.py`  
  Validates session token handling for `/chat` in the mock app. It covers bearer tokens in the Authorization header, cookie-based sessions when enabled, and the 401 response when no token is provided.
  Command:
  ```bash
  make be-test-security-session
  ```

### Backend voice tests
- `backend/tests/test_persona_voice.py`  
  Ensures the mock `/chat` endpoint produces first-person phrasing. It asserts the response structure and does a content sanity check (TLDR presence, no stray filter lines, and presence of first-person pronouns).

- `backend/tests/test_normalize_question_punct.py`  
  Focuses on punctuation handling and name permutations for first-person normalization. It checks possessive handling (straight/curly apostrophes), bare-name substitutions, and ensures certain inputs are never modified (emails, handles, paths).

Command:
```bash
make be-test-voice
```

### Backend unit tests
- `backend/tests/test_llm_prompt.py`  
  Validates prompt construction and token-budget trimming. It checks that the system and user prompts include required content and that chunk trimming behaves correctly for tight and exact budgets.
  Command:
  ```bash
  make be-test-llm-prompt
  ```

- `backend/tests/test_keys_store.py`  
  Covers access-key hashing, fingerprinting, and lookup behavior. It asserts correct handling of expired/revoked keys, missing keys, and duplicate fingerprint detection.
  Command:
  ```bash
  make be-test-keys-store
  ```

- `backend/tests/test_create_access_key_cli.py`  
  Verifies the admin CLI create/revoke flows using a fake Firestore client. It checks JSON output, stored fields, and error handling for missing keys.
  Command:
  ```bash
  make be-test-create-access-key-cli
  ```

- `backend/tests/test_build_datapoints.py`  
  Exercises the datapoint writer helpers for Matching Engine. It verifies restricts mapping, JSONL output structure, and gzip output behavior.
  Command:
  ```bash
  make be-test-build_datapoints
  ```

- `backend/tests/test_load_backend_env.py`  
  Validates `load_backend_env` handling for required keys, overrides, and environment variable expansion.
  Command:
  ```bash
  make be-test-load-backend-env
  ```

- `backend/tests/test_pack_and_push_processing.py`  
  Exercises pack-and-push processing helpers, including chunk loading, record serialization, and manifest writing.
  Command:
  ```bash
  make be-test-pack-and-push-processing
  ```

- `backend/tests/test_retrieval_vector.py`  
  Tests vector search adapter logic. It confirms normalization of embeddings, guard rails (empty vectors, zero `top_k`), and that configuring the client swaps the active implementation.
  Command:
  ```bash
  make be-test-retrieval-vector
  ```

### Integration tests (real services)
- `backend/tests/test_integration_real_backend.py`  
  Runs against a real backend (`uvicorn api.main:app`) with live credentials.
  Sub-tests:
  - `/health`: no access key required; no live vector required.
  - `/auth/key-login`: requires `ACCESS_KEY_PLAINTEXT`; no live vector required.
  - `/chat` response contract + first-person phrasing: requires `ACCESS_KEY_PLAINTEXT` and a live vector.
  - `/chat` rate limiting (503 allowed until limiter triggers): requires `ACCESS_KEY_PLAINTEXT`; no live vector required.
  Command:
  ```bash
  make be-test-int
  ```

- `backend/tests/test_cors_deployment.py`  
  Checks CORS allow/deny behavior on a deployed backend using the configured Hosting origin.
  Command:
  ```bash
  make be-test-cors-deploy
  ```

- `backend/tests/test_vector_search_integration.py`  
  Optional live Vertex AI Matching Engine round-trip. It is skipped by default and only runs when `RUN_VERTEX_SEARCH_TEST=1` and a test embedding is provided.
  Command:
  ```bash
  make be-test-vector-live
  ```

### Test support
- `backend/tests/conftest.py`  
  Provides default environment variables and fixtures so tests can run consistently without manual configuration.
- `backend/pytest.ini` - `integration` marker configuration.
