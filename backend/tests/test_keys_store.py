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


def test_hash_and_verify_roundtrip() -> None:
    """Verify hash/verify roundtrip for access keys.

    What is tested:
        hash_key, verify_key, and compute_key_fingerprint outputs.
    How it's tested:
        Hash a sample key and verify success/failure against valid/invalid input.
    Expected result format:
        Hash differs, verify passes for valid key, fails for invalid, fingerprint len 64.
    """
    plain_access_key = ACCESS_KEY_SAMPLE
    hashed_access_key = keys.hash_key(plain_access_key)

    assert hashed_access_key != plain_access_key
    assert keys.verify_key(plain_access_key, hashed_access_key)
    assert not keys.verify_key(INVALID_ACCESS_KEY, hashed_access_key)
    assert len(keys.compute_key_fingerprint(plain_access_key)) == 64


def test_get_record_valid(access_key_store: AccessKeyStoreProtocol) -> None:
    """Verify valid keys return an access record with label.

    What is tested:
        get_record_by_plain_key behavior for a valid, unexpired key.
    How it's tested:
        Add a key with label and expiry, then fetch by plain key.
    Expected result format:
        Record id matches stored id and label equals ACCESS_KEY_LABEL_DEMO.
    """
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


def test_get_record_expired(access_key_store: AccessKeyStoreProtocol) -> None:
    """Verify expired keys raise HTTP 401 with key_expired detail.

    What is tested:
        get_record_by_plain_key behavior for expired keys.
    How it's tested:
        Add an expired key and expect an HTTPException on lookup.
    Expected result format:
        Exception status is 401 and detail equals KEY_EXPIRED_DETAIL.
    """
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


def test_get_record_revoked(access_key_store: AccessKeyStoreProtocol) -> None:
    """Verify revoked keys raise HTTP 401 with key_revoked detail.

    What is tested:
        get_record_by_plain_key behavior for revoked keys.
    How it's tested:
        Add a revoked key and expect an HTTPException on lookup.
    Expected result format:
        Exception status is 401 and detail equals KEY_REVOKED_DETAIL.
    """
    access_key_store.add_plain_key(REVOKED_ACCESS_KEY, revoked=True)
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(REVOKED_ACCESS_KEY)
    assert exc.value.status_code == UNAUTHORIZED_STATUS_CODE
    assert exc.value.detail == KEY_REVOKED_DETAIL


def test_get_record_missing_key(access_key_store: AccessKeyStoreProtocol) -> None:
    """Verify missing keys raise HTTP 401 with invalid_key detail.

    What is tested:
        get_record_by_plain_key behavior when no record exists.
    How it's tested:
        Look up a missing key and capture the HTTPException.
    Expected result format:
        Exception status is 401 and detail equals INVALID_KEY_DETAIL.
    """
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(MISSING_ACCESS_KEY)
    assert exc.value.status_code == UNAUTHORIZED_STATUS_CODE
    assert exc.value.detail == INVALID_KEY_DETAIL


def test_duplicate_fingerprint_hard_fail(
    access_key_store: AccessKeyStoreProtocol,
) -> None:
    """Verify duplicate fingerprints raise HTTP 500 and skip last transaction.

    What is tested:
        get_record_by_plain_key handling of duplicate fingerprint conflicts.
    How it's tested:
        Add two keys with the same fingerprint and attempt lookup.
    Expected result format:
        Exception status is 500, detail includes duplicate fingerprint code/message,
        and last_transaction_used is False.
    """
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


def test_expiry_uses_timezone_aware_datetime(
    access_key_store: AccessKeyStoreProtocol,
) -> None:
    """Verify expiry timestamps are timezone-aware and enforced.

    What is tested:
        Access key expiry normalization and expiration enforcement.
    How it's tested:
        Add aware/naive expiry keys, then confirm tzinfo normalization and expiry error.
    Expected result format:
        Retrieved records have tzinfo set, and expired key raises key_expired detail.
    """
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
