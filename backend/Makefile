# Default interpreter. Override like: make install PYTHON=python3.11
PYTHON ?= python3.13

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
TEST_ENV ?= LLM_BACKEND=deterministic

include ../make/env.mk

DATASET_DIR ?= $(dir $(DATAPOINTS_FILE))
DATASET_VERSION ?= $(notdir $(patsubst %/,%,$(DATASET_DIR)))
DATASET_FOLDER := datasets
DATASET_GCS_PREFIX ?= $(DATASET_FOLDER)/$(DATASET_VERSION)

.PHONY: help install security-install security security-static security-bandit security-deps security-semgrep run run-debug mock eval-gating test-smoke test-core test-voice test-int test-cors-deploy test-vector-live test-build_datapoints test-auth-key-login test-create-access-key-cli test-keys-store test-llm-prompt test-thinking-gating test-llm-gating test-main-chat-logging test-security-session test-load-backend-env test-pack-and-push-processing test-answer-deduplication test-retrieval-bm25 test-greeting-intent check envs clean build_datapoints create-access-key dataset-version-check dataset-chunks dataset-datapoints dataset-upload dataset-pointer-update

help:
	@echo "Targets:"
	@echo "  install      - create venv and install deps"
	@echo "  security-install - install pinned security tooling into venv"
	@echo "  security     - run local backend security scans"
	@echo "  security-static - run ruff security rules (S)"
	@echo "  security-bandit - run bandit over backend source"
	@echo "  security-deps - run pip-audit on backend requirements"
	@echo "  security-semgrep - run semgrep security scan if semgrep exists"
	@echo "  run          - start integrated API (api.main:app) on :8080 (requires envs)"
	@echo "  mock         - start mock API on :8080"
	@echo "  eval-gating   - run dataset-based gating eval (pass ARGS='...')"
	@echo "  docker-build - build local backend image"
	@echo "  docker-run   - run local backend image with mounted secrets"
	@echo "  test-smoke   - run pytest smoke tests against mock app"
	@echo "  test-core    - run all mock-safe backend tests"
	@echo "  test-voice   - run persona voice + normalization tests (mock)"
	@echo "  test-int     - run integration tests against cloud backend (requires GCP + uvicorn)"
	@echo "  test-cors-deploy - run deployed CORS integration checks (requires integrated backend URL)"
	@echo "  test-auth-key-login - run auth key login tests"
	@echo "  test-create-access-key-cli - run access key CLI tests"
	@echo "  create-access-key - run access key CLI (pass ARGS='create ...')"
	@echo "  test-keys-store - run key store tests"
	@echo "  test-llm-prompt - run LLM prompt construction tests"
	@echo "  test-thinking-gating - run thinking-budget gating tests"
	@echo "  test-llm-gating - run deterministic llm-gating tests"
	@echo "  test-main-chat-logging - run chat success structured logging tests"
	@echo "  test-security-session - run session security tests"
	@echo "  test-load-backend-env - run load_backend_env tests"
	@echo "  test-pack-and-push-processing - run pack_and_push processing tests"
	@echo "  test-answer-deduplication - run answer deduplication/formatting tests"
	@echo "  test-retrieval-bm25 - run BM25 token filtering and indexing tests"
	@echo "  test-greeting-intent - run greeting-only intent and bypass tests"
	@echo "  check        - quick import check"
	@echo "  build_datapoints - build embedding datapoints JSONL for upsert"
	@echo "  dataset-chunks - build chunks.jsonl.gz in DATAPOINTS_FILE directory"
	@echo "  dataset-datapoints - build datapoints.jsonl + manifest.json"
	@echo "  dataset-upload - upload dataset folder to GCS"
	@echo "  dataset-pointer-update - update datasets/current.json in GCS"
	@echo "  test-build_datapoints - run datapoint helper unit tests"
	@echo "  test-retrieval-vector - run vector adapter unit tests"
	@echo "  envs         - print required env variables"
	@echo "  clean        - remove caches"
	@echo "  clean-all    - remove caches and venv"

$(VENV)/bin/activate: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	touch $(VENV)/bin/activate

install: $(VENV)/bin/activate

security-install: $(VENV)/bin/activate
	$(PIP) install -r requirements-security.txt

security: security-static security-bandit security-deps

security-static: security-install
	$(PY) -m ruff check --select S api jobs scripts

security-bandit: security-install
	$(PY) -m bandit -q -r api jobs scripts -x tests

security-deps: security-install
	$(PY) -m pip_audit -r requirements.txt

security-semgrep:
	@if command -v semgrep >/dev/null 2>&1; then \
		semgrep --config p/security-audit --error --exclude private --exclude private-template .; \
	else \
		echo "semgrep is not installed, skipping semgrep scan."; \
		echo "Install semgrep with: pipx install semgrep"; \
	fi

run: $(VENV)/bin/activate
	$(RUN_ENV) $(PY) -m uvicorn api.main:app --reload --port 8080

run-debug: RUN_ENV=APP_LOG_LEVEL=debug
run-debug: run

docker-build:
	# Use backend/ as the build context so Dockerfile paths (requirements.txt, etc.) resolve correctly
	docker build -t $(LOCAL_IMAGE) -f $(DOCKERFILE) $(BACKEND_DIR)

docker-run:
	docker run --rm -p 8080:8080 \
		--env-file "$(COMMON_ENV)" \
		--env-file "$(BACKEND_ENV)" \
		-e PRIVATE_DIR=/private \
		-v "$(PRIVATE_DIR)":/private \
		$(LOCAL_IMAGE)

docker-run-mock:
	docker run --rm -p 8080:8080 \
		--env-file "$(COMMON_ENV)" \
		--env-file "$(BACKEND_ENV)" \
		-e PRIVATE_DIR=/private \
		-v "$(PRIVATE_DIR)":/private \
		$(LOCAL_IMAGE) \
		uvicorn api.mock:app --host 0.0.0.0 --port 8080

mock: $(VENV)/bin/activate
	LLM_BACKEND=deterministic $(PY) -m uvicorn api.mock:app --reload --port 8080

eval-gating: $(VENV)/bin/activate
	$(PY) scripts/eval_gating.py $(ARGS)

pack_and_push: $(VENV)/bin/activate
	$(PY) jobs/pack_and_push.py

build_datapoints: $(VENV)/bin/activate
	$(PY) jobs/build_datapoints.py

dataset-version-check:
	@test -n "$(DATASET_VERSION)" || { echo "DATASET_VERSION is required (e.g. v13)."; exit 1; }
	@printf "%s" "$(DATASET_VERSION)" | grep -Eq '^v[0-9A-Za-z_-]+$$' || { echo "Invalid DATASET_VERSION '$(DATASET_VERSION)'; expected v followed by [0-9A-Za-z_-]."; exit 1; }

dataset-chunks: dataset-version-check $(VENV)/bin/activate
	$(PY) jobs/pack_and_push.py --output-dir "$(DATASET_DIR)" --skip-upload

dataset-datapoints: dataset-version-check $(VENV)/bin/activate
	$(PY) jobs/build_datapoints.py

dataset-upload:
	gcloud storage cp "$(DATASET_DIR)/datapoints.jsonl" "gs://$(BUCKET_NAME)/$(DATASET_GCS_PREFIX)/datapoints.jsonl"
	gcloud storage cp "$(DATASET_DIR)/chunks.jsonl.gz" "gs://$(BUCKET_NAME)/$(DATASET_GCS_PREFIX)/chunks.jsonl.gz"
	gcloud storage cp "$(DATASET_DIR)/manifest.json" "gs://$(BUCKET_NAME)/$(DATASET_GCS_PREFIX)/manifest.json"
	@echo "GCS browser: https://console.cloud.google.com/storage/browser/$(BUCKET_NAME)"

dataset-pointer-update:
	printf '{ "version": "%s" }\n' "$(DATASET_VERSION)" | gcloud storage cp - "gs://$(BUCKET_NAME)/$(DATASET_FOLDER)/current.json"

test-build_datapoints: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_build_datapoints.py

test-smoke: $(VENV)/bin/activate
	LLM_BACKEND=deterministic $(PY) -m pytest -q $(PYTEST_ARGS) tests/test_smoke.py

test-core: test-smoke test-voice test-auth-key-login test-security-session test-keys-store test-llm-prompt test-create-access-key-cli test-build_datapoints test-retrieval-vector test-thinking-gating test-llm-gating test-main-chat-logging test-load-backend-env test-pack-and-push-processing test-answer-deduplication test-retrieval-bm25 test-greeting-intent test-duration-domain-config test-duration-routing test-duration-interval-merge test-rag-duration-deterministic-flow

test-voice: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_persona_voice.py tests/test_normalize_question_punct.py

test-int: $(VENV)/bin/activate
	$(PY) -m pytest -q -rs -m integration tests/test_integration_real_backend.py

test-cors-deploy: $(VENV)/bin/activate
	$(PY) -m pytest -q -rs -s -m integration tests/test_cors_deployment.py

test-vector-live: $(VENV)/bin/activate
	embedding=`$(PY) -m scripts.emit_test_embedding` || exit 1; \
	test -n "$$embedding" || { echo "Failed to derive embedding from $(DATAPOINTS_FILE)"; exit 1; }; \
	RUN_VERTEX_SEARCH_TEST=1 VERTEX_TEST_EMBEDDING="$$embedding" $(PY) -m pytest -q tests/test_vector_search_integration.py

test-retrieval-vector: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_retrieval_vector.py

test-auth-key-login: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_auth_key_login.py

test-create-access-key-cli: $(VENV)/bin/activate
	PYTHONPATH=.. $(TEST_ENV) $(PY) -m pytest -q tests/test_create_access_key_cli.py

create-access-key: $(VENV)/bin/activate
	$(PY) scripts/create_access_key.py $(ARGS)

test-keys-store: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_keys_store.py

test-llm-prompt: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_llm_prompt.py

test-thinking-gating: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_thinking_gating.py

test-llm-gating: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_llm_gating.py

test-main-chat-logging: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_main_chat_logging.py

test-security-session: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_security_session.py

test-load-backend-env: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_load_backend_env.py

test-pack-and-push-processing: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_pack_and_push_processing.py

test-answer-deduplication: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_answer_deduplication.py

test-retrieval-bm25: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_retrieval_bm25.py

test-greeting-intent: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_greeting_intent.py

test-duration-domain-config: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_duration_domain_config.py

test-duration-routing: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_duration_routing.py

test-duration-interval-merge: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_duration_interval_merge.py

test-rag-duration-deterministic-flow: $(VENV)/bin/activate
	$(TEST_ENV) $(PY) -m pytest -q tests/test_rag_duration_deterministic_flow.py

check: $(VENV)/bin/activate
	$(PY) -c "import api.main, api.mock, api.retrieval, api.llm, api.settings"

clean:
	rm -rf __pycache__ .pytest_cache api/__pycache__ tests/__pycache__

clean-all: clean
	rm -rf .venv
