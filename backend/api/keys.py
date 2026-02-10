from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

import bcrypt
from fastapi import HTTPException, status
from google.cloud import firestore

from .settings import settings

logger = logging.getLogger("api.keys")


@dataclass
class KeyRecord:
    id: str
    expires_at: datetime
    label: Optional[str] = None


class KeyStore(Protocol):
    def get_record_by_plain_key(self, plain_key: str) -> KeyRecord: ...


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_key_fingerprint(plain_key: str) -> str:
    """Return a SHA-256 fingerprint for lookup without storing the plaintext."""
    return hashlib.sha256((plain_key or "").encode("utf-8")).hexdigest()


def hash_key(plain_key: str) -> str:
    """Hash a plaintext key with bcrypt for storage."""
    if not isinstance(plain_key, str) or not plain_key:
        raise ValueError("plain_key must be a non-empty string")
    hashed = bcrypt.hashpw(plain_key.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_key(plain_key: str, key_hash: str) -> bool:
    """Check a plaintext key against a bcrypt hash."""
    if not plain_key or not key_hash:
        return False
    try:
        return bool(bcrypt.checkpw(plain_key.encode("utf-8"), key_hash.encode("utf-8")))
    except ValueError:
        return False


class JsonKeyStore:
    """JSON-backed key store for local/mock auth."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("mock key store missing: %s", self.path)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_missing")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.exception("mock key store invalid json: %s", self.path)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_invalid")
        if not isinstance(data, dict):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_invalid")
        return data

    def _parse_expires_at(self, value) -> datetime:
        if not value:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_invalid")
        if isinstance(value, datetime):
            expires_at = value
        elif isinstance(value, str):
            ts = value
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            try:
                expires_at = datetime.fromisoformat(ts)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_invalid")
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_invalid")
        return _ensure_aware(expires_at)

    def get_record_by_plain_key(self, plain_key: str) -> KeyRecord:
        if not plain_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_key")

        data = self._load()
        entries = data.get("keys", [])
        if not isinstance(entries, list):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_store_invalid")

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("key") != plain_key:
                continue
            if entry.get("revoked", False):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="key_revoked")

            expires_at = self._parse_expires_at(entry.get("expires_at"))
            if _now() >= expires_at:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="key_expired")

            key_id = str(entry.get("id") or "mock")
            label = entry.get("label")
            return KeyRecord(id=key_id, expires_at=expires_at, label=label)

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_key")


class FirestoreKeyStore:
    """Firestore-backed key store. Documents live in the `access_keys` collection."""

    def __init__(
        self,
        client: Optional[firestore.Client] = None,
        collection_name: str = "access_keys",
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._collection = None

    @property
    def collection(self):
        if self._collection is None:
            client = self._client or firestore.Client(project=settings.PROJECT_ID)
            self._collection = client.collection(self._collection_name)
        return self._collection

    def _query_by_fingerprint(self, fingerprint: str) -> Sequence:
        query = self.collection.where("key_fingerprint", "==", fingerprint).limit(2)
        return list(query.stream())

    def _get_doc_ref_for_id(self, doc_id: str):
        return self.collection.document(doc_id)

    def _transaction_get(self, transaction, doc_ref):
        return transaction.get(doc_ref)

    def _transaction_update(self, transaction, doc_ref, data: dict) -> None:
        transaction.update(doc_ref, data)

    def _run_transaction(self, fn: Callable):
        collection = self.collection
        transaction = collection._client.transaction()

        @firestore.transactional
        def _wrapped(txn):
            return fn(txn)

        return _wrapped(transaction)

    def get_record_by_plain_key(self, plain_key: str) -> KeyRecord:
        if not plain_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_key")

        fingerprint = compute_key_fingerprint(plain_key)
        snapshots = self._query_by_fingerprint(fingerprint)
        if not snapshots:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_key")
        if len(snapshots) > 1:
            doc_ids = [str(doc.id) for doc in snapshots]
            logger.error("duplicate access key fingerprint=%s doc_ids=%s", fingerprint, doc_ids)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "duplicate_fingerprint", "message": "Multiple access keys share the same fingerprint"},
            )

        doc = snapshots[0]
        doc_id = str(doc.id)

        def _txn(transaction):
            doc_ref = self._get_doc_ref_for_id(doc_id)
            snapshot = self._transaction_get(transaction, doc_ref)
            if not getattr(snapshot, "exists", True):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_key")
            payload = snapshot.to_dict() or {}

            key_hash = payload.get("key_hash")
            if not key_hash or not verify_key(plain_key, key_hash):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_key")

            if payload.get("revoked", False):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="key_revoked")

            expires_at = payload.get("expires_at")
            if not expires_at:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_key")
            expires_at = _ensure_aware(expires_at)
            if _now() >= expires_at:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="key_expired")

            return KeyRecord(id=doc_id, expires_at=expires_at, label=payload.get("label"))

        try:
            return self._run_transaction(_txn)
        except HTTPException:
            raise
        except Exception:
            logger.exception("failed to validate access key for fingerprint %s", fingerprint)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="key_validation_failed"
            )

    def revoke_key(self, key_id: str) -> None:
        self.collection.document(key_id).update({"revoked": True})


_key_store: Optional[KeyStore] = None


def get_key_store() -> KeyStore:
    global _key_store
    if _key_store is None:
        _key_store = FirestoreKeyStore()
    return _key_store


def set_key_store(store: Optional[KeyStore]) -> None:
    """Override the key store (primarily for tests)."""
    global _key_store
    _key_store = store


def verify_plain_key_and_get_record(plain_key: str, *, key_store: Optional[KeyStore] = None) -> KeyRecord:
    """Validate plain key via Firestore and return its metadata."""
    store = key_store or get_key_store()
    return store.get_record_by_plain_key(plain_key)
