from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence, TypeVar, cast

import bcrypt
from fastapi import HTTPException, status
from google.cloud import firestore  # type: ignore[reportMissingTypeStubs]

from .settings import settings

logger = logging.getLogger("api.keys")

T = TypeVar("T")

ACCESS_KEYS_COLLECTION_NAME = "access_keys"
KEYS_PAYLOAD_FIELD = "keys"
KEY_FIELD_NAME = "key"
KEY_FINGERPRINT_FIELD = "key_fingerprint"
KEY_HASH_FIELD = "key_hash"
KEY_ID_FIELD = "id"
KEY_LABEL_FIELD = "label"
KEY_EXPIRES_AT_FIELD = "expires_at"
KEY_REVOKED_FIELD = "revoked"
DEFAULT_MOCK_KEY_ID = "mock"

ERROR_CODE_KEY_STORE_MISSING = "key_store_missing"
ERROR_CODE_KEY_STORE_INVALID = "key_store_invalid"
ERROR_CODE_MISSING_KEY = "missing_key"
ERROR_CODE_INVALID_KEY = "invalid_key"
ERROR_CODE_KEY_REVOKED = "key_revoked"
ERROR_CODE_KEY_EXPIRED = "key_expired"
ERROR_CODE_DUPLICATE_FINGERPRINT = "duplicate_fingerprint"
ERROR_CODE_KEY_VALIDATION_FAILED = "key_validation_failed"

DUPLICATE_FINGERPRINT_MESSAGE = "Multiple access keys share the same fingerprint"


@dataclass
class KeyRecord:
    id: str
    expires_at: datetime
    label: Optional[str] = None


class KeyStore(Protocol):
    def get_record_by_plain_key(self, plain_key: str) -> KeyRecord: ...


class FirestoreDocumentSnapshot(Protocol):
    id: str

    @property
    def exists(self) -> bool: ...

    def to_dict(self) -> dict[str, Any] | None: ...


class FirestoreDocumentReference(Protocol):
    def update(self, *args: Any, **kwargs: Any) -> Any: ...


class FirestoreTransaction(Protocol):
    def get(self, doc_ref: FirestoreDocumentReference) -> FirestoreDocumentSnapshot: ...

    def update(self, doc_ref: FirestoreDocumentReference, data: dict[str, Any]) -> Any: ...


class FirestoreQuery(Protocol):
    def where(self, field_path: str, op_string: str, value: Any) -> "FirestoreQuery": ...

    def limit(self, count: int) -> "FirestoreQuery": ...

    def stream(self) -> Iterable[FirestoreDocumentSnapshot]: ...


class FirestoreCollection(Protocol):
    def where(self, field_path: str, op_string: str, value: Any) -> FirestoreQuery: ...

    def document(self, doc_id: str | None = None) -> FirestoreDocumentReference: ...

    @property
    def _client(self) -> Any: ...


class FirestoreClient(Protocol):
    def collection(self, name: str) -> Any: ...

    def transaction(self) -> FirestoreTransaction: ...


def _ensure_aware(dt: datetime) -> datetime:
    """Return timezone-aware datetime normalized to UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    """Return the current UTC time.
    """
    return datetime.now(timezone.utc)


def compute_key_fingerprint(plain_key: str) -> str:
    """Return a SHA-256 fingerprint for lookup without storing the plaintext.

    Inputs:
        plain_key: The raw API key provided by the caller.

    Outputs:
        A hex-encoded SHA-256 digest of the key for indexed lookup.
    """
    return hashlib.sha256((plain_key or "").encode("utf-8")).hexdigest()


def hash_key(plain_key: str) -> str:
    """Hash a plaintext key with bcrypt for storage.

    Inputs:
        plain_key: The raw API key to be stored.

    Outputs:
        A bcrypt hash suitable for storage.

    Edge cases:
        Raises ValueError if the key is missing or not a string.
    """
    if not plain_key:
        raise ValueError("plain_key must be a non-empty string")
    hashed = bcrypt.hashpw(plain_key.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_key(plain_key: str, key_hash: str) -> bool:
    """Check a plaintext key against a bcrypt hash.

    Inputs:
        plain_key: The raw API key provided by a client.
        key_hash: A stored bcrypt hash to validate against.

    Outputs:
        True if the key matches the hash; otherwise False.

    Edge cases:
        Returns False for missing inputs or invalid hash formats.
    """
    if not plain_key or not key_hash:
        return False
    try:
        return bool(
            bcrypt.checkpw(plain_key.encode("utf-8"), key_hash.encode("utf-8"))
        )
    except ValueError:
        return False


class JsonKeyStore:
    """JSON-backed key store for local/mock auth.

    This store expects a JSON file with a `keys` array of entries that include
    plaintext keys. It is intended for local development only.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        """Load and validate the JSON key store payload.

        Outputs:
            A dictionary representing the parsed JSON payload.

        Edge cases:
            Raises HTTP 500 when the file is missing, unreadable, or invalid.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("mock key store missing: %s", self.path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_STORE_MISSING,
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.exception("mock key store invalid json: %s", self.path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_STORE_INVALID,
            )
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_STORE_INVALID,
            )
        return cast(dict[str, Any], data)

    def _parse_expires_at(self, raw_expires_at: object) -> datetime:
        """Parse and normalize expiration timestamps from the JSON payload.

        Inputs:
            raw_expires_at: A datetime or ISO-8601 string from the JSON payload.

        Outputs:
            A timezone-aware datetime normalized to UTC.

        Edge cases:
            Raises HTTP 500 if the value is missing or malformed.
        """
        if not raw_expires_at:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_STORE_INVALID,
            )
        if isinstance(raw_expires_at, datetime):
            expires_at = raw_expires_at
        elif isinstance(raw_expires_at, str):
            timestamp = raw_expires_at
            if timestamp.endswith("Z"):
                timestamp = timestamp[:-1] + "+00:00"
            try:
                expires_at = datetime.fromisoformat(timestamp)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=ERROR_CODE_KEY_STORE_INVALID,
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_STORE_INVALID,
            )
        return _ensure_aware(expires_at)

    def get_record_by_plain_key(self, plain_key: str) -> KeyRecord:
        """Fetch a key record by matching the plaintext key.

        Inputs:
            plain_key: The raw API key provided by the caller.

        Outputs:
            A KeyRecord with metadata for the key.

        Edge cases:
            Raises HTTP 401 for invalid, expired, or revoked keys.
            Raises HTTP 500 for malformed store payloads.
        """
        if not plain_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_CODE_MISSING_KEY,
            )

        data = self._load()
        entries_raw = data.get(KEYS_PAYLOAD_FIELD, [])
        if not isinstance(entries_raw, list):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_STORE_INVALID,
            )
        entries_raw = cast(list[object], entries_raw)

        entries: list[dict[str, Any]] = []
        for entry in entries_raw:
            entry_obj: object = entry
            if isinstance(entry_obj, dict):
                entries.append(cast(dict[str, Any], entry_obj))
        for entry_data in entries:
            if entry_data.get(KEY_FIELD_NAME) != plain_key:
                continue
            if entry_data.get(KEY_REVOKED_FIELD, False):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_KEY_REVOKED,
                )

            expires_at = self._parse_expires_at(entry_data.get(KEY_EXPIRES_AT_FIELD))
            if _now() >= expires_at:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_KEY_EXPIRED,
                )

            key_id = str(entry_data.get(KEY_ID_FIELD) or DEFAULT_MOCK_KEY_ID)
            label = cast(Optional[str], entry_data.get(KEY_LABEL_FIELD))
            return KeyRecord(id=key_id, expires_at=expires_at, label=label)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_CODE_INVALID_KEY,
        )


class FirestoreKeyStore:
    """Firestore-backed key store.

    Documents live in the configured collection, defaulting to `access_keys`.
    """

    def __init__(
        self,
        client: Optional[FirestoreClient] = None,
        collection_name: str = ACCESS_KEYS_COLLECTION_NAME,
        client_factory: Optional[Callable[[], FirestoreClient]] = None,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._client_factory = client_factory
        self._collection: Optional[Any] = None

    @property
    def collection(self) -> FirestoreCollection:
        """Return the cached Firestore collection, initializing if needed.

        Outputs:
            A Firestore collection reference for access key documents.
        """
        if self._collection is None:
            client_factory = self._client_factory or (
                lambda: firestore.Client(project=settings.PROJECT_ID)
            )
            client = self._client or client_factory()
            self._collection = client.collection(self._collection_name)
        return cast(FirestoreCollection, self._collection)

    def _query_by_fingerprint(self, fingerprint: str) -> Sequence[FirestoreDocumentSnapshot]:
        """Query the collection for a matching key fingerprint.

        Inputs:
            fingerprint: The SHA-256 fingerprint of the plaintext key.

        Outputs:
            A list of matching document snapshots, limited to two.
        """
        query = (
            self.collection.where(KEY_FINGERPRINT_FIELD, "==", fingerprint)
            .limit(2)
        )
        return list(query.stream())

    def _get_doc_ref_for_id(self, doc_id: str) -> FirestoreDocumentReference:
        """Return a document reference for the provided document id."""
        return self.collection.document(doc_id)

    def _transaction_get(
        self,
        transaction: FirestoreTransaction,
        doc_ref: FirestoreDocumentReference,
    ) -> FirestoreDocumentSnapshot:
        """Return a document snapshot within a Firestore transaction.

        Some Firestore client versions return a generator even for a single
        document reference, so normalize to a single snapshot.
        """
        snapshot = transaction.get(doc_ref)
        if hasattr(snapshot, "to_dict"):
            return snapshot
        try:
            return next(iter(snapshot))
        except StopIteration:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_CODE_INVALID_KEY,
            )

    def _transaction_update(
        self,
        transaction: FirestoreTransaction,
        doc_ref: FirestoreDocumentReference,
        data: dict[str, Any],
    ) -> None:
        """Update a document within a Firestore transaction."""
        transaction.update(doc_ref, data)

    def _run_transaction(self, fn: Callable[[FirestoreTransaction], T]) -> T:
        """Run a function inside a Firestore transaction.

        Inputs:
            fn: A callable that accepts a transaction and returns a value.

        Outputs:
            The return value of the transaction callable.
        """
        collection = self.collection
        transaction = collection._client.transaction() # type: ignore[reportPrivateUsage]

        @firestore.transactional # type: ignore[reportUnknownMemberType]
        def _wrapped(txn: FirestoreTransaction) -> T:
            return fn(txn)

        return _wrapped(transaction)

    def get_record_by_plain_key(self, plain_key: str) -> KeyRecord:
        """Fetch a key record by validating the plaintext key.

        Inputs:
            plain_key: The raw API key provided by the caller.

        Outputs:
            A KeyRecord with metadata for the key.

        Edge cases:
            Raises HTTP 401 for invalid, expired, or revoked keys.
            Raises HTTP 500 for duplicate fingerprints or unexpected failures.
        """
        if not plain_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_CODE_MISSING_KEY,
            )

        fingerprint = compute_key_fingerprint(plain_key)
        snapshots = self._query_by_fingerprint(fingerprint)
        if not snapshots:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_CODE_INVALID_KEY,
            )
        if len(snapshots) > 1:
            doc_ids = [str(doc.id) for doc in snapshots]
            logger.error(
                "duplicate access key fingerprint=%s doc_ids=%s",
                fingerprint,
                doc_ids,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": ERROR_CODE_DUPLICATE_FINGERPRINT,
                    "message": DUPLICATE_FINGERPRINT_MESSAGE,
                },
            )

        doc = snapshots[0]
        doc_id = str(doc.id)

        def _txn(transaction: FirestoreTransaction) -> KeyRecord:
            doc_ref = self._get_doc_ref_for_id(doc_id)
            snapshot = self._transaction_get(transaction, doc_ref)
            if not getattr(snapshot, "exists", True):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_INVALID_KEY,
                )
            payload = snapshot.to_dict() or {}

            key_hash = payload.get(KEY_HASH_FIELD)
            if not key_hash or not verify_key(plain_key, key_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_INVALID_KEY,
                )

            if payload.get(KEY_REVOKED_FIELD, False):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_KEY_REVOKED,
                )

            expires_at = payload.get(KEY_EXPIRES_AT_FIELD)
            if not isinstance(expires_at, datetime):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_INVALID_KEY,
                )
            expires_at = _ensure_aware(expires_at)
            if _now() >= expires_at:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ERROR_CODE_KEY_EXPIRED,
                )

            return KeyRecord(
                id=doc_id,
                expires_at=expires_at,
                label=cast(Optional[str], payload.get(KEY_LABEL_FIELD)),
            )

        try:
            return self._run_transaction(_txn)
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "failed to validate access key for fingerprint %s",
                fingerprint,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_CODE_KEY_VALIDATION_FAILED,
            )

    def revoke_key(self, key_id: str) -> None:
        """Revoke an access key document by id.

        Inputs:
            key_id: The Firestore document id of the access key.
        """
        self.collection.document(key_id).update({KEY_REVOKED_FIELD: True})


_key_store: Optional[KeyStore] = None


def get_key_store() -> KeyStore:
    """Return the singleton key store implementation.

    Outputs:
        A KeyStore instance, defaulting to FirestoreKeyStore.
    """
    global _key_store
    if _key_store is None:
        _key_store = FirestoreKeyStore()
    return _key_store


def set_key_store(store: Optional[KeyStore]) -> None:
    """Override the key store implementation (primarily for tests).

    Inputs:
        store: The KeyStore instance to set, or None to reset.
    """
    global _key_store
    _key_store = store


def verify_plain_key_and_get_record(
    plain_key: str,
    *,
    key_store: Optional[KeyStore] = None,
) -> KeyRecord:
    """Validate a plaintext key and return its metadata.

    Inputs:
        plain_key: The raw API key provided by the caller.
        key_store: Optional KeyStore override, mainly for tests.

    Outputs:
        A KeyRecord containing key metadata.
    """
    store = key_store or get_key_store()
    return store.get_record_by_plain_key(plain_key)
