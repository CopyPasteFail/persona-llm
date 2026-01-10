"""Tests for access key storage behavior, including hashing and expiry handling."""

from datetime import datetime, timedelta, timezone
from typing import Protocol, cast

import pytest
from fastapi import HTTPException

from api import keys

ACCESS_KEY_SAMPLE = "sample-access-key"
INVALID_ACCESS_KEY = "invalid-access-key"
VALID_ACCESS_KEY = "valid-key-1"
EXPIRED_ACCESS_KEY = "expired-key"
REVOKED_ACCESS_KEY = "revoked-key"
MISSING_ACCESS_KEY = "does-not-exist"
DUPLICATE_ACCESS_KEY = "dup-key"
TIMEZONE_AWARE_ACCESS_KEY = "aware-expiry"
TIMEZONE_NAIVE_ACCESS_KEY = "naive-expiry"
TIMEZONE_AWARE_EXPIRED_ACCESS_KEY = "aware-expired"

ACCESS_KEY_LABEL_DEMO = "demo"
ACCESS_KEY_LABEL_FIRST = "first"
ACCESS_KEY_LABEL_SECOND = "second"

EXPIRY_OFFSET_MINUTES = 5
EXPIRY_BUFFER_MINUTES = 1
EXPIRED_OFFSET_SECONDS = 1

UNAUTHORIZED_STATUS_CODE = 401
INTERNAL_ERROR_STATUS_CODE = 500

KEY_EXPIRED_DETAIL = "key_expired"
KEY_REVOKED_DETAIL = "key_revoked"
INVALID_KEY_DETAIL = "invalid_key"
DUPLICATE_FINGERPRINT_CODE = "duplicate_fingerprint"
DUPLICATE_FINGERPRINT_MESSAGE = "Multiple access keys share the same fingerprint"


class AccessKeyRecordProtocol(Protocol):
    """Minimal access key record interface used by these tests."""

    id: str
    label: str | None
    expires_at: datetime


class AccessKeyStoreProtocol(Protocol):
    """Minimal access key store interface used by these tests."""

    last_transaction_used: bool

    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str: ...

    def get_record_by_plain_key(self, plain_key: str) -> AccessKeyRecordProtocol: ...


# Verify hashing produces a distinct value and only the original key verifies.
def test_hash_and_verify_roundtrip() -> None:
    plain_access_key = ACCESS_KEY_SAMPLE
    hashed_access_key = keys.hash_key(plain_access_key)

    assert hashed_access_key != plain_access_key
    assert keys.verify_key(plain_access_key, hashed_access_key)
    assert not keys.verify_key(INVALID_ACCESS_KEY, hashed_access_key)
    assert len(keys.compute_key_fingerprint(plain_access_key)) == 64


# Ensure fetching a valid key returns the expected record and label.
def test_get_record_valid(access_key_store: AccessKeyStoreProtocol) -> None:
    plain_access_key = VALID_ACCESS_KEY
    expires_at_utc = datetime.now(timezone.utc) + timedelta(
        minutes=EXPIRY_OFFSET_MINUTES
    )
    record_id: str = access_key_store.add_plain_key(
        plain_access_key,
        label=ACCESS_KEY_LABEL_DEMO,
        expires_at=expires_at_utc,
    )

    record: AccessKeyRecordProtocol = access_key_store.get_record_by_plain_key(
        plain_access_key
    )
    assert record.id == record_id
    assert record.label == ACCESS_KEY_LABEL_DEMO


# Confirm expired keys raise a 401 with the expected error detail.
def test_get_record_expired(access_key_store: AccessKeyStoreProtocol) -> None:
    access_key_store.add_plain_key(
        EXPIRED_ACCESS_KEY,
        expires_at=datetime.now(timezone.utc) - timedelta(
            minutes=EXPIRY_BUFFER_MINUTES
        ),
    )
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(EXPIRED_ACCESS_KEY)
    assert exc.value.status_code == UNAUTHORIZED_STATUS_CODE
    assert exc.value.detail == KEY_EXPIRED_DETAIL


# Confirm revoked keys raise a 401 with the expected error detail.
def test_get_record_revoked(access_key_store: AccessKeyStoreProtocol) -> None:
    access_key_store.add_plain_key(REVOKED_ACCESS_KEY, revoked=True)
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(REVOKED_ACCESS_KEY)
    assert exc.value.status_code == UNAUTHORIZED_STATUS_CODE
    assert exc.value.detail == KEY_REVOKED_DETAIL


# Confirm missing keys raise a 401 with the expected error detail.
def test_get_record_missing_key(access_key_store: AccessKeyStoreProtocol) -> None:
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(MISSING_ACCESS_KEY)
    assert exc.value.status_code == UNAUTHORIZED_STATUS_CODE
    assert exc.value.detail == INVALID_KEY_DETAIL


# Ensure duplicate fingerprints raise a 500 and do not use the last transaction.
def test_duplicate_fingerprint_hard_fail(
    access_key_store: AccessKeyStoreProtocol,
) -> None:
    plain_access_key = DUPLICATE_ACCESS_KEY
    access_key_store.add_plain_key(plain_access_key, label=ACCESS_KEY_LABEL_FIRST)
    access_key_store.add_plain_key(plain_access_key, label=ACCESS_KEY_LABEL_SECOND)

    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(plain_access_key)

    assert exc.value.status_code == INTERNAL_ERROR_STATUS_CODE
    error_detail = cast(dict[str, str], exc.value.detail)
    assert error_detail["code"] == DUPLICATE_FINGERPRINT_CODE
    assert error_detail["message"] == DUPLICATE_FINGERPRINT_MESSAGE
    assert access_key_store.last_transaction_used is False


# Verify expiry timestamps are timezone-aware and enforced for expired keys.
def test_expiry_uses_timezone_aware_datetime(
    access_key_store: AccessKeyStoreProtocol,
) -> None:
    aware_expires_at_utc = datetime.now(timezone.utc) + timedelta(
        minutes=EXPIRY_BUFFER_MINUTES
    )
    access_key_store.add_plain_key(
        TIMEZONE_AWARE_ACCESS_KEY,
        expires_at=aware_expires_at_utc,
    )
    aware_record: AccessKeyRecordProtocol = access_key_store.get_record_by_plain_key(
        TIMEZONE_AWARE_ACCESS_KEY
    )
    assert aware_record.expires_at.tzinfo is not None

    naive_expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=EXPIRY_BUFFER_MINUTES)
    ).replace(tzinfo=None)
    access_key_store.add_plain_key(
        TIMEZONE_NAIVE_ACCESS_KEY,
        expires_at=naive_expires_at,
    )
    naive_record: AccessKeyRecordProtocol = access_key_store.get_record_by_plain_key(
        TIMEZONE_NAIVE_ACCESS_KEY
    )
    assert naive_record.expires_at.tzinfo is not None

    expired_expires_at_utc = datetime.now(timezone.utc) - timedelta(
        seconds=EXPIRED_OFFSET_SECONDS
    )
    access_key_store.add_plain_key(
        TIMEZONE_AWARE_EXPIRED_ACCESS_KEY,
        expires_at=expired_expires_at_utc,
    )
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(
            TIMEZONE_AWARE_EXPIRED_ACCESS_KEY
        )
    assert exc.value.detail == KEY_EXPIRED_DETAIL
