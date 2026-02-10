# backend/tests/conftest.py
import os

# Only set if not already provided in the environment
os.environ.setdefault("PERSONA_NAME", "Alex Taylor")  # ≤ 4 words, ≤ 50 chars

# Minimal but valid infra values that pass your validators
os.environ.setdefault("PROJECT_ID", "proj-test-123")
os.environ.setdefault("REGION", "us-central1")
os.environ.setdefault(
    "INDEX_ENDPOINT_ID",
    "projects/proj-test-123/locations/us-central1/indexEndpoints/1234567890",
)
os.environ.setdefault("DEPLOYED_INDEX_ID", "deployed-test-1")
os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("CHUNKS_PATH", "chunks-abc.jsonl.gz")
os.environ.setdefault("API_KEY", "test-key-123")

# Token limits and timeout as strings so Pydantic coerces to int
os.environ.setdefault("MAX_INPUT_TOKENS", "3000")
os.environ.setdefault("MAX_OUTPUT_TOKENS", "180")
os.environ.setdefault("REQ_TIMEOUT_MS", "20000")
