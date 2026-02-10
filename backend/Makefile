# Default interpreter. Override like: make install PYTHON=python3.11
PYTHON ?= python3.13

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help install run mock test test-voice test-int check envs clean datapoints

help:
	@echo "Targets:"
	@echo "  install      - create venv and install deps"
	@echo "  run          - start real API skeleton on :8000 (requires envs)"
	@echo "  mock         - start mock API on :8080"
	@echo "  test         - run pytest smoke tests against mock app"
	@echo "  test-voice   - run persona voice + normalization tests (mock)"
	@echo "  test-int     - run integration tests against real backend (requires GCP + uvicorn)"
	@echo "  check        - quick import check"
	@echo "  datapoints   - build embedding datapoints JSONL for upsert"
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
	$(PY) -m uvicorn api.main:app --reload --port 8000

mock: $(VENV)/bin/activate
	$(PY) -m uvicorn api.mock:app --reload --port 8080

pack_and_push: $(VENV)/bin/activate
	$(PY) jobs/pack_and_push.py

datapoints: $(VENV)/bin/activate
	$(PY) jobs/build_datapoints.py $(ARGS)

test: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_smoke.py

test-voice: $(VENV)/bin/activate
	$(PY) -m pytest -q tests/test_persona_voice.py tests/test_normalize_question_punct.py

test-int: $(VENV)/bin/activate
	REAL_BACKEND_URL=http://localhost:8000 $(PY) -m pytest -q -m integration tests/test_integration_real_backend.py

check: $(VENV)/bin/activate
	$(PY) -c "import api.main, api.mock, api.retrieval, api.llm, api.settings"

envs:
	@echo "PROJECT_ID, REGION, INDEX_ENDPOINT_ID, DEPLOYED_INDEX_ID, BUCKET_NAME, CHUNKS_PATH, API_KEY, MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, REQ_TIMEOUT_MS"

clean:
	rm -rf __pycache__ .pytest_cache api/__pycache__ tests/__pycache__

clean-all: clean
	rm -rf .venv
