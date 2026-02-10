# Default interpreter. Override like: make install PYTHON=python3.11
PYTHON ?= python3.13

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

include ../make/env.mk

.PHONY: help install run run-debug mock test test-core test-voice test-int test-vector-live test-build_datapoints test-auth-key-login test-create-access-key-cli test-keys-store test-llm-prompt test-security-session check envs clean build_datapoints

help:
	@echo "Targets:"
	@echo "  install      - create venv and install deps"
	@echo "  run          - start real API skeleton on :8000 (requires envs)"
	@echo "  mock         - start mock API on :8080"
	@echo "  docker-build - build local backend image"
	@echo "  docker-run   - run local backend image with mounted secrets"
	@echo "  test         - run pytest smoke tests against mock app"
	@echo "  test-core    - run all mock-safe backend tests"
	@echo "  test-voice   - run persona voice + normalization tests (mock)"
	@echo "  test-int     - run integration tests against real backend (requires GCP + uvicorn)"
	@echo "  test-auth-key-login - run auth key login tests"
	@echo "  test-create-access-key-cli - run access key CLI tests"
	@echo "  test-keys-store - run key store tests"
	@echo "  test-llm-prompt - run LLM prompt construction tests"
	@echo "  test-security-session - run session security tests"
	@echo "  check        - quick import check"
	@echo "  build_datapoints - build embedding datapoints JSONL for upsert"
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

run: $(VENV)/bin/activate
	$(RUN_ENV) $(PY) -m uvicorn api.main:app --reload --port 8000

run-debug: RUN_ENV=RETRIEVAL_DEBUG=1 UVICORN_LOG_LEVEL=debug
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
	$(PY) -m uvicorn api.mock:app --reload --port 8080

pack_and_push: $(VENV)/bin/activate
	$(PY) jobs/pack_and_push.py

build_datapoints: $(VENV)/bin/activate
	$(PY) jobs/build_datapoints.py

test-build_datapoints: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_build_datapoints.py

test: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_smoke.py

test-core: test test-voice test-auth-key-login test-security-session test-keys-store test-llm-prompt test-create-access-key-cli test-build_datapoints test-retrieval-vector

test-voice: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_persona_voice.py tests/test_normalize_question_punct.py

test-int: $(VENV)/bin/activate
	$(PY) -m pytest -q -m integration tests/test_integration_real_backend.py

test-vector-live: $(VENV)/bin/activate
	embedding=`$(PY) -m scripts.emit_test_embedding` || exit 1; \
	test -n "$$embedding" || { echo "Failed to derive embedding from $(DATAPOINTS_FILE)"; exit 1; }; \
	RUN_VERTEX_SEARCH_TEST=1 VERTEX_TEST_EMBEDDING="$$embedding" $(PY) -m pytest -q tests/test_vector_search_integration.py

test-retrieval-vector: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_retrieval_vector.py

test-auth-key-login: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_auth_key_login.py

test-create-access-key-cli: $(VENV)/bin/activate
	PYTHONPATH=.. $(PY) -m pytest -q tests/test_create_access_key_cli.py

test-keys-store: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_keys_store.py

test-llm-prompt: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_llm_prompt.py

test-security-session: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_security_session.py

check: $(VENV)/bin/activate
	$(PY) -c "import api.main, api.mock, api.retrieval, api.llm, api.settings"

clean:
	rm -rf __pycache__ .pytest_cache api/__pycache__ tests/__pycache__

clean-all: clean
	rm -rf .venv
