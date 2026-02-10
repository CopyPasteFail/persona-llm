# backend/tests/conftest.py
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Only set if not already provided in the environment
os.environ.setdefault("PERSONA_NAME", "Alex Taylor")  # ≤ 4 words, ≤ 50 chars

# Minimal but valid infra values that pass your validators
os.environ.setdefault("PROJECT_ID", "proj-test-123")
os.environ.setdefault("REGION", "us-central1")
os.environ.setdefault(
    "INDEX_ENDPOINT_ID",
    "1234567890",
)
os.environ.setdefault("DEPLOYED_INDEX_ID", "deployed-test-1")
os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("CHUNKS_PATH", "chunks-abc.jsonl.gz")
os.environ.setdefault("API_KEY", "test-key-123")

# Token limits and timeout as strings so Pydantic coerces to int
os.environ.setdefault("MAX_INPUT_TOKENS", "3000")
os.environ.setdefault("MAX_OUTPUT_TOKENS", "180")
os.environ.setdefault("REQ_TIMEOUT_MS", "20000")

from api import keys  # noqa: E402


class _FakeDoc:
    def __init__(self, doc_id: str, payload: dict):
        self.id = doc_id
        self._payload = payload
        self.exists = True

    def to_dict(self):
        return self._payload


class _FakeDocRef:
    def __init__(self, store: "InMemoryFirestoreStore", doc_id: str):
        self.id = doc_id
        self._store = store

    def get(self, transaction=None):
        return self._store._docs_by_id[self.id]

    def update(self, data: dict, transaction=None) -> None:
        doc = self._store._docs_by_id[self.id]
        payload = doc.to_dict()
        for key, value in data.items():
            updated = self._apply_value(payload.get(key), value)
            payload[key] = updated

    @staticmethod
    def _apply_value(current, value):
        increment_by = None
        if value.__class__.__name__ == "Increment":
            increment_by = getattr(value, "value", None)
            if increment_by is None:
                increment_by = getattr(value, "_operand", None)
            if increment_by is None:
                increment_by = getattr(value, "_Increment__operand", None)
            if increment_by is None:
                increment_by = 1
        if increment_by is not None:
            base = current or 0
            return base + increment_by
        return value


class _FakeTransaction:
    def __init__(self, store: "InMemoryFirestoreStore"):
        self._store = store

    def get(self, doc_ref: _FakeDocRef):
        return doc_ref.get(transaction=self)

    def update(self, doc_ref: _FakeDocRef, data: dict) -> None:
        doc_ref.update(data, transaction=self)


class InMemoryFirestoreStore(keys.FirestoreKeyStore):
    """Test double for FirestoreKeyStore that keeps documents in memory."""

    def __init__(self):
        super().__init__(client=None)
        self._docs_by_fp: dict[str, list[_FakeDoc]] = {}
        self._docs_by_id: dict[str, _FakeDoc] = {}
        self.last_transaction_used = False

    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str:
        exp = expires_at or (datetime.now(timezone.utc) + timedelta(hours=1))
        fingerprint = keys.compute_key_fingerprint(plain_key)
        payload = {
            "key_hash": keys.hash_key(plain_key),
            "key_fingerprint": fingerprint,
            "expires_at": exp,
            "revoked": revoked,
            "label": label,
        }
        doc_id = f"doc-{len(self._docs_by_id) + 1}"
        doc = _FakeDoc(doc_id, payload)
        self._docs_by_id[doc_id] = doc
        self._docs_by_fp.setdefault(fingerprint, []).append(doc)
        return doc_id

    def _query_by_fingerprint(self, fingerprint: str):
        matches = self._docs_by_fp.get(fingerprint, [])
        return matches[:2]

    def _get_doc_ref_for_id(self, doc_id: str):
        return _FakeDocRef(self, doc_id)

    def _run_transaction(self, fn):
        self.last_transaction_used = True
        txn = _FakeTransaction(self)
        return fn(txn)

    def revoke_key(self, key_id: str) -> None:
        doc = self._docs_by_id.get(key_id)
        if doc:
            doc.to_dict()["revoked"] = True


@pytest.fixture
def access_key_store():
    store = InMemoryFirestoreStore()
    keys.set_key_store(store)
    try:
        yield store
    finally:
        keys.set_key_store(None)
