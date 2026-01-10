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

from api import keys


DocumentPayload = dict[str, object]


class _FakeDocument:
    # Test helper: store a document payload and expose Firestore-style access.
    def __init__(self, doc_id: str, payload: DocumentPayload):
        self.id = doc_id
        self._payload: DocumentPayload = payload
        self.exists = True

    # Test helper: return the stored document payload as a dict.
    def to_dict(self) -> DocumentPayload:
        return self._payload


class _FakeDocumentReference:
    # Test helper: point to a document in the in-memory store by id.
    def __init__(self, store: "InMemoryFirestoreStore", doc_id: str):
        self.id = doc_id
        self._store = store

    # Test helper: fetch the document from the store, matching Firestore API.
    def get(self, transaction: Optional["_FakeTransaction"] = None) -> _FakeDocument:
        return self._store.get_document_by_id(self.id)

    # Test helper: apply updates to the stored document payload.
    def update(
        self, data: DocumentPayload, transaction: Optional["_FakeTransaction"] = None
    ) -> None:
        stored_doc = self._store.get_document_by_id(self.id)
        payload = stored_doc.to_dict()
        for field_name, new_value in data.items():
            updated_value = self._apply_value(payload.get(field_name), new_value)
            payload[field_name] = updated_value

    # Test helper: interpret Firestore Increment values and apply them to fields.
    @staticmethod
    def _apply_value(current: object | None, value: object) -> object:
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
    # Test helper: hold a reference to the in-memory store for transactions.
    def __init__(self, store: "InMemoryFirestoreStore"):
        self._store = store

    # Test helper: fetch a document within the transaction.
    def get(self, doc_ref: _FakeDocumentReference) -> _FakeDocument:
        return doc_ref.get(transaction=self)

    # Test helper: update a document within the transaction.
    def update(self, doc_ref: _FakeDocumentReference, data: DocumentPayload) -> None:
        doc_ref.update(data, transaction=self)


class InMemoryFirestoreStore(keys.FirestoreKeyStore):
    """Test double for FirestoreKeyStore that keeps documents in memory."""

    # Test helper: initialize empty in-memory document stores and flags.
    def __init__(self):
        super().__init__(client=None)
        self._docs_by_fp: dict[str, list[_FakeDocument]] = {}
        self._docs_by_id: dict[str, _FakeDocument] = {}
        self.last_transaction_used = False

    # Test helper: get a stored document by id for the fake Firestore API.
    def get_document_by_id(self, doc_id: str) -> _FakeDocument:
        return self._docs_by_id[doc_id]

    # Test helper: add a key document with optional metadata for lookup by tests.
    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str:
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

    # Test helper: return at most a small set of matching documents by fingerprint.
    def _query_by_fingerprint(self, fingerprint: str) -> list[_FakeDocument]:
        matches = self._docs_by_fp.get(fingerprint, [])
        return matches[:MAX_FINGERPRINT_MATCHES]

    # Test helper: return a document reference for a given id.
    def _get_doc_ref_for_id(self, doc_id: str) -> Any:
        return _FakeDocumentReference(self, doc_id)

    # Test helper: simulate Firestore transactions and record usage for tests.
    def _run_transaction(self, fn: Callable[[_FakeTransaction], Any]) -> Any:
        self.last_transaction_used = True
        transaction = _FakeTransaction(self)
        return fn(transaction)

    # Test helper: mark a key as revoked in the stored document payload.
    def revoke_key(self, key_id: str) -> None:
        document = self._docs_by_id.get(key_id)
        if document:
            document.to_dict()["revoked"] = True


@pytest.fixture
# Test fixture: provide an in-memory key store and reset it after each test.
def access_key_store():
    store = InMemoryFirestoreStore()
    keys.set_key_store(store)
    try:
        yield store
    finally:
        keys.set_key_store(None)
