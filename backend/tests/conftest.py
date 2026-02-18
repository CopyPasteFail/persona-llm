"""Test configuration and in-memory Firestore test doubles for key auth."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Only set if not already provided in the environment
PERSONA_NAME_DEFAULT = "Alex Taylor"
PROJECT_ID_DEFAULT = "proj-test-123"
REGION_DEFAULT = "us-central1"
INDEX_ENDPOINT_ID_DEFAULT = "1234567890"
DEPLOYED_INDEX_ID_DEFAULT = "deployed-test-1"
BUCKET_NAME_DEFAULT = "test-bucket"
CHUNKS_PATH_DEFAULT = "chunks-abc.jsonl.gz"
API_KEY_DEFAULT = "test-key-123"
MAX_INPUT_TOKENS_DEFAULT = "3000"
MAX_OUTPUT_TOKENS_DEFAULT = "180"
REQUEST_TIMEOUT_MS_DEFAULT = "20000"
DOC_ID_PREFIX = "doc-"
DEFAULT_EXPIRATION_HOURS = 1
DEFAULT_INCREMENT = 1
MAX_FINGERPRINT_MATCHES = 2
RUN_INTEGRATION_TESTS_ENV_VAR = "RUN_INTEGRATION_TESTS"
INTEGRATION_SKIP_REASON = (
    f"Integration tests are skipped by default. Set {RUN_INTEGRATION_TESTS_ENV_VAR}=1 to enable."
)
INTEGRATION_TEST_FILE_PREFIX = "test_integration_"
INTEGRATION_MARKER = "integration"

os.environ.setdefault("PERSONA_NAME", PERSONA_NAME_DEFAULT)  # ≤ 4 words, ≤ 50 chars

# Minimal but valid infra values that pass your validators
os.environ.setdefault("PROJECT_ID", PROJECT_ID_DEFAULT)
os.environ.setdefault("REGION", REGION_DEFAULT)
os.environ.setdefault(
    "INDEX_ENDPOINT_ID",
    INDEX_ENDPOINT_ID_DEFAULT,
)
os.environ.setdefault("DEPLOYED_INDEX_ID", DEPLOYED_INDEX_ID_DEFAULT)
os.environ.setdefault("BUCKET_NAME", BUCKET_NAME_DEFAULT)
os.environ.setdefault("CHUNKS_PATH", CHUNKS_PATH_DEFAULT)
os.environ.setdefault("API_KEY", API_KEY_DEFAULT)

# Token limits and timeout as strings so Pydantic coerces to int
os.environ.setdefault("MAX_INPUT_TOKENS", MAX_INPUT_TOKENS_DEFAULT)
os.environ.setdefault("MAX_OUTPUT_TOKENS", MAX_OUTPUT_TOKENS_DEFAULT)
os.environ.setdefault("REQ_TIMEOUT_MS", REQUEST_TIMEOUT_MS_DEFAULT)

# Keep core tests deterministic and independent of developer shell env.
# Some test suites assert citations exist in the deterministic mock flow; that
# expectation assumes LLM-call gating is disabled.
os.environ["ENABLE_LLM_CALL_GATING"] = "0"

from api import keys


DocumentPayload = dict[str, object]


def _find_unmarked_integration_file_tests(collected_items: list[pytest.Item]) -> list[str]:
    """Return node ids for tests in integration-named files missing marker.

    Inputs:
    - collected_items: Pytest items discovered during test collection.

    Outputs:
    - Node-id strings for tests whose filename begins with
      ``test_integration_`` but that are missing ``@pytest.mark.integration``.

    Edge cases:
    - Uses basename-only matching so absolute/relative path differences do not
      affect enforcement.
    """

    violations: list[str] = []
    for collected_item in collected_items:
        path_attr = getattr(collected_item, "path", None)
        item_path = (
            Path(str(path_attr))
            if path_attr is not None
            else Path(str(collected_item.fspath))
        )
        if not item_path.name.startswith(INTEGRATION_TEST_FILE_PREFIX):
            continue
        if INTEGRATION_MARKER in collected_item.keywords:
            continue
        violations.append(collected_item.nodeid)
    return violations


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip integration tests unless explicitly enabled via environment variable.

    Inputs:
    - items: Collected pytest items for the current run.

    Outputs:
    - None. Applies skip markers in-place when integration tests are disabled.

    Edge cases:
    - When RUN_INTEGRATION_TESTS=1, no skip markers are applied.
    """

    del session
    del config

    unmarked_integration_tests = _find_unmarked_integration_file_tests(items)
    if unmarked_integration_tests:
        raise pytest.UsageError(
            "All tests in files named 'test_integration_*.py' must be marked with "
            "@pytest.mark.integration. Missing marker on: "
            f"{', '.join(unmarked_integration_tests)}"
        )

    if os.getenv(RUN_INTEGRATION_TESTS_ENV_VAR) == "1":
        return

    skip_integration_marker = pytest.mark.skip(reason=INTEGRATION_SKIP_REASON)
    for collected_item in items:
        if INTEGRATION_MARKER in collected_item.keywords:
            collected_item.add_marker(skip_integration_marker)


class _FakeDocument:
    def __init__(self, doc_id: str, payload: DocumentPayload):
        """Test helper: store a document payload and expose Firestore-style access."""
        self.id = doc_id
        self._payload: DocumentPayload = payload
        self.exists = True

    def to_dict(self) -> DocumentPayload:
        """Test helper: return the stored document payload as a dict."""
        return self._payload


class _FakeDocumentReference:
    def __init__(self, store: "InMemoryFirestoreStore", doc_id: str):
        """Test helper: point to a document in the in-memory store by id."""
        self.id = doc_id
        self._store = store

    def get(self, transaction: Optional["_FakeTransaction"] = None) -> _FakeDocument:
        """Test helper: fetch the document from the store, matching Firestore API."""
        return self._store.get_document_by_id(self.id)

    def update(
        self, data: DocumentPayload, transaction: Optional["_FakeTransaction"] = None
    ) -> None:
        """Test helper: apply updates to the stored document payload."""
        stored_doc = self._store.get_document_by_id(self.id)
        payload = stored_doc.to_dict()
        for field_name, new_value in data.items():
            updated_value = self._apply_value(payload.get(field_name), new_value)
            payload[field_name] = updated_value

    @staticmethod
    def _apply_value(current: object | None, value: object) -> object:
        """Test helper: interpret Firestore Increment values and apply them to fields."""
        increment_by: int | None = None
        if value.__class__.__name__ == "Increment":
            increment_by = getattr(value, "value", None)
            if increment_by is None:
                increment_by = getattr(value, "_operand", None)
            if increment_by is None:
                increment_by = getattr(value, "_Increment__operand", None)
            if increment_by is None:
                increment_by = DEFAULT_INCREMENT
        if increment_by is not None:
            current_value = current if isinstance(current, int) else 0
            return current_value + increment_by
        return value


class _FakeTransaction:
    def __init__(self, store: "InMemoryFirestoreStore"):
        """Test helper: hold a reference to the in-memory store for transactions."""
        self._store = store

    def get(self, doc_ref: _FakeDocumentReference) -> _FakeDocument:
        """Test helper: fetch a document within the transaction."""
        return doc_ref.get(transaction=self)

    def update(self, doc_ref: _FakeDocumentReference, data: DocumentPayload) -> None:
        """Test helper: update a document within the transaction."""
        doc_ref.update(data, transaction=self)


class InMemoryFirestoreStore(keys.FirestoreKeyStore):
    """Test double for FirestoreKeyStore that keeps documents in memory."""

    def __init__(self):
        """Test helper: initialize empty in-memory document stores and flags."""
        super().__init__(client=None)
        self._docs_by_fp: dict[str, list[_FakeDocument]] = {}
        self._docs_by_id: dict[str, _FakeDocument] = {}
        self.last_transaction_used = False

    def get_document_by_id(self, doc_id: str) -> _FakeDocument:
        """Test helper: get a stored document by id for the fake Firestore API."""
        return self._docs_by_id[doc_id]

    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str:
        """Test helper: add a key document with optional metadata for lookup by tests."""
        expiration_time = expires_at or (
            datetime.now(timezone.utc) + timedelta(hours=DEFAULT_EXPIRATION_HOURS)
        )
        fingerprint = keys.compute_key_fingerprint(plain_key)
        payload: DocumentPayload = {
            "key_hash": keys.hash_key(plain_key),
            "key_fingerprint": fingerprint,
            "expires_at": expiration_time,
            "revoked": revoked,
            "label": label,
        }
        doc_id = f"{DOC_ID_PREFIX}{len(self._docs_by_id) + 1}"
        document = _FakeDocument(doc_id, payload)
        self._docs_by_id[doc_id] = document
        self._docs_by_fp.setdefault(fingerprint, []).append(document)
        return doc_id

    def _query_by_fingerprint(self, fingerprint: str) -> list[_FakeDocument]:
        """Test helper: return at most a small set of matching documents by fingerprint."""
        matches = self._docs_by_fp.get(fingerprint, [])
        return matches[:MAX_FINGERPRINT_MATCHES]

    def _get_doc_ref_for_id(self, doc_id: str) -> Any:
        """Test helper: return a document reference for a given id."""
        return _FakeDocumentReference(self, doc_id)

    def _run_transaction(self, fn: Callable[[_FakeTransaction], Any]) -> Any:
        """Test helper: simulate Firestore transactions and record usage for tests."""
        self.last_transaction_used = True
        transaction = _FakeTransaction(self)
        return fn(transaction)

    def revoke_key(self, key_id: str) -> None:
        """Test helper: mark a key as revoked in the stored document payload."""
        document = self._docs_by_id.get(key_id)
        if document:
            document.to_dict()["revoked"] = True


@pytest.fixture
def access_key_store():
    """Test fixture: provide an in-memory key store and reset it after each test."""
    store = InMemoryFirestoreStore()
    keys.set_key_store(store)
    try:
        yield store
    finally:
        keys.set_key_store(None)
