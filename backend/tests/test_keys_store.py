from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from api import keys


def test_hash_and_verify_roundtrip():
    plain = "sample-access-key"
    hashed = keys.hash_key(plain)
    assert hashed != plain
    assert keys.verify_key(plain, hashed)
    assert not keys.verify_key("wrong", hashed)
    assert len(keys.compute_key_fingerprint(plain)) == 64


def test_get_record_valid(access_key_store):
    plain = "valid-key-1"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    doc_id = access_key_store.add_plain_key(plain, label="demo", expires_at=expires_at)

    record = access_key_store.get_record_by_plain_key(plain)
    assert record.id == doc_id
    assert record.label == "demo"

    payload = access_key_store._docs_by_fp[keys.compute_key_fingerprint(plain)][0].to_dict()
    assert payload["used_count"] == 1


def test_get_record_expired(access_key_store):
    access_key_store.add_plain_key(
        "expired-key",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key("expired-key")
    assert exc.value.status_code == 401
    assert exc.value.detail == "key_expired"


def test_get_record_revoked(access_key_store):
    access_key_store.add_plain_key("revoked-key", revoked=True)
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key("revoked-key")
    assert exc.value.status_code == 401
    assert exc.value.detail == "key_revoked"


def test_get_record_exhausted(access_key_store):
    access_key_store.add_plain_key("limited-key", used_count=3, max_uses=3)
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key("limited-key")
    assert exc.value.status_code == 401
    assert exc.value.detail == "key_exhausted"


def test_get_record_missing_key(access_key_store):
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key("does-not-exist")
    assert exc.value.status_code == 401
    assert exc.value.detail == "invalid_key"


def test_duplicate_fingerprint_hard_fail(access_key_store):
    plain = "dup-key"
    access_key_store.add_plain_key(plain, label="first")
    access_key_store.add_plain_key(plain, label="second")

    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(plain)

    assert exc.value.status_code == 500
    assert exc.value.detail["code"] == "duplicate_fingerprint"
    assert exc.value.detail["message"] == "Multiple access keys share the same fingerprint"
    assert access_key_store.last_transaction_used is False


def test_used_count_atomic_increment_and_max_uses(access_key_store):
    plain = "limited-key"
    access_key_store.add_plain_key(plain, used_count=0, max_uses=2)

    record1 = access_key_store.get_record_by_plain_key(plain)
    assert record1.id.startswith("doc-")
    payload = access_key_store._docs_by_fp[keys.compute_key_fingerprint(plain)][0].to_dict()
    assert payload["used_count"] == 1

    record2 = access_key_store.get_record_by_plain_key(plain)
    assert record2.id == record1.id
    assert payload["used_count"] == 2

    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key(plain)
    assert exc.value.status_code == 401
    assert exc.value.detail == "key_exhausted"
    assert access_key_store.last_transaction_used is True


def test_expiry_uses_timezone_aware_datetime(access_key_store):
    aware_expiry = datetime.now(timezone.utc) + timedelta(minutes=1)
    access_key_store.add_plain_key("aware-expiry", expires_at=aware_expiry)
    record = access_key_store.get_record_by_plain_key("aware-expiry")
    assert record.expires_at.tzinfo is not None

    naive_expiry = (datetime.now(timezone.utc) + timedelta(minutes=1)).replace(tzinfo=None)
    access_key_store.add_plain_key("naive-expiry", expires_at=naive_expiry)
    naive_record = access_key_store.get_record_by_plain_key("naive-expiry")
    assert naive_record.expires_at.tzinfo is not None

    expired_aware = datetime.now(timezone.utc) - timedelta(seconds=1)
    access_key_store.add_plain_key("aware-expired", expires_at=expired_aware)
    with pytest.raises(HTTPException) as exc:
        access_key_store.get_record_by_plain_key("aware-expired")
    assert exc.value.detail == "key_expired"
