from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

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


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

            max_uses = _coerce_int(payload.get("max_uses"))
            used_count = _coerce_int(payload.get("used_count") or 0) or 0
            if max_uses is not None and used_count >= max_uses:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="key_exhausted")

            self._transaction_update(transaction, doc_ref, {"used_count": firestore.Increment(1)})
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


_key_store: Optional[FirestoreKeyStore] = None


def get_key_store() -> FirestoreKeyStore:
    global _key_store
    if _key_store is None:
        _key_store = FirestoreKeyStore()
    return _key_store


def set_key_store(store: Optional[FirestoreKeyStore]) -> None:
    """Override the key store (primarily for tests)."""
    global _key_store
    _key_store = store


def verify_plain_key_and_get_record(plain_key: str, *, key_store: Optional[FirestoreKeyStore] = None) -> KeyRecord:
    """Validate plain key via Firestore and return its metadata."""
    store = key_store or get_key_store()
    return store.get_record_by_plain_key(plain_key)
