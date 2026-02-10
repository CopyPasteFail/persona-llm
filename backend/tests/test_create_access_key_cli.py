"""Tests for the access key CLI create/revoke flows using an in-memory fake client."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Protocol, cast

import pytest

from backend.scripts import create_access_key


class CreateAccessKeyModule(Protocol):
    DEFAULT_COLLECTION: str

    def run_create(
        self,
        args: argparse.Namespace,
        *,
        client: Optional["FakeClient"] = None,
        now: datetime | None = None,
    ) -> int: ...

    def run_revoke(
        self,
        args: argparse.Namespace,
        *,
        client: Optional["FakeClient"] = None,
        now: datetime | None = None,
    ) -> int: ...


typed_create_access_key = cast(CreateAccessKeyModule, create_access_key)

DEFAULT_EXPIRES_IN = "7d"
DEMO_LABEL = "demo"
FIXED_SECRET_VALUE = "fixed-secret"
MISSING_KEY_ID = "missing"
REVOKED_BY_USER = "tester"
REVOKE_KEY_ID = "abc123"
CREATE_EXPIRES_IN = "1d"
AUTO_ID_PREFIX = "auto-"
EXPECTED_EXPIRES_AT_PREFIX = "2024-01-02T12:00:00"
REVOKED_OUTPUT_SUBSTRING = "revoked"
NOT_FOUND_ERROR_SUBSTRING = "not found"
MISSING_DOCUMENT_ERROR_MESSAGE = "missing document"
COMMAND_CREATE = "create"
COMMAND_REVOKE = "revoke"
ARG_LABEL = "label"
ARG_EXPIRES_IN = "expires_in"
ARG_EXPIRES_AT = "expires_at"
ARG_PRINT_JSON = "print_json"
ARG_PROJECT = "project"
ARG_KEY_ID = "key_id"
ARG_REVOKED_BY = "revoked_by"
ARG_COMMAND = "command"
REVOKED_FIELD = "revoked"
REVOKED_BY_FIELD = "revoked_by"
REVOKED_AT_FIELD = "revoked_at"
LABEL_FIELD = "label"
KEY_ID_FIELD = "key_id"
KEY_FINGERPRINT_FIELD = "key_fingerprint"
KEY_PLAINTEXT_FIELD = "key_plaintext"
EXPIRES_AT_FIELD = "expires_at"
CREATE_TIMESTAMP = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
REVOKE_TIMESTAMP = datetime(2024, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
JSON_PRINT_ENABLED = True

DocumentData = dict[str, Any]
CollectionStore = dict[str, DocumentData]


class FakeSnapshot:
    # Store whether a fake document exists and provide its data.
    def __init__(self, data: DocumentData | None = None):
        self._data = data
        self.exists = data is not None

    # Return the snapshot data in the same shape as the real client.
    def to_dict(self) -> DocumentData:
        return dict(self._data) if self._data is not None else {}


class FakeDocRef:
    # Represent a document reference and operate on the parent collection data.
    def __init__(self, collection: "FakeCollection", doc_id: str):
        self._collection = collection
        self.id = doc_id

    # Set document data for the referenced document ID.
    def set(self, data: Mapping[str, Any]) -> None:
        self._collection.set_document_data(self.id, data)

    # Read document data into a fake snapshot.
    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._collection.get_document_data(self.id))

    # Update document data, raising if the document is missing.
    def update(self, data: Mapping[str, Any]) -> None:
        self._collection.update_document_data(self.id, data)


class FakeCollection:
    # Create an in-memory collection to mimic Firestore behavior.
    def __init__(self):
        self._data: CollectionStore = {}
        self._counter = 0

    # Return a document reference, auto-assigning IDs when none are provided.
    def document(self, doc_id: str | None = None) -> FakeDocRef:
        if doc_id is None:
            self._counter += 1
            doc_id = f"auto-{self._counter}"
        return FakeDocRef(self, doc_id)

    # Store document data under the given ID.
    def set_document_data(self, doc_id: str, data: Mapping[str, Any]) -> None:
        self._data[doc_id] = dict(data)

    # Fetch document data for the given ID.
    def get_document_data(self, doc_id: str) -> DocumentData | None:
        return self._data.get(doc_id)

    # Update document data under the given ID.
    def update_document_data(self, doc_id: str, data: Mapping[str, Any]) -> None:
        if doc_id not in self._data:
            raise KeyError(MISSING_DOCUMENT_ERROR_MESSAGE)
        self._data[doc_id].update(data)


class FakeClient:
    # Provide collections backed by in-memory data stores.
    def __init__(self):
        self._collections: dict[str, FakeCollection] = {}

    # Return or create a named collection.
    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


# Build CLI args in a consistent shape for tests.
def make_args_for_command(command: str, **overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        ARG_LABEL: None,
        ARG_EXPIRES_IN: DEFAULT_EXPIRES_IN,
        ARG_EXPIRES_AT: None,
        ARG_PRINT_JSON: False,
        ARG_PROJECT: None,
        ARG_KEY_ID: None,
        ARG_REVOKED_BY: None,
    }
    defaults.update(overrides)
    defaults[ARG_COMMAND] = command
    return argparse.Namespace(**defaults)


# Verify create prints JSON and stores a non-revoked key with expected fields.
def test_create_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_client = FakeClient()
    current_time = CREATE_TIMESTAMP

    # Provide a deterministic secret so the plaintext assertion is stable.
    def fixed_token_urlsafe(length: int) -> str:
        return FIXED_SECRET_VALUE

    monkeypatch.setattr(
        typed_create_access_key,
        "secrets",
        SimpleNamespace(token_urlsafe=fixed_token_urlsafe),
    )

    create_args = make_args_for_command(
        COMMAND_CREATE,
        label=DEMO_LABEL,
        expires_in=CREATE_EXPIRES_IN,
        print_json=JSON_PRINT_ENABLED,
    )
    exit_code = typed_create_access_key.run_create(
        create_args,
        client=fake_client,
        now=current_time,
    )
    assert exit_code == 0

    stdout_text: str = capsys.readouterr().out.strip()
    payload: DocumentData = json.loads(stdout_text)
    assert payload[KEY_ID_FIELD].startswith(AUTO_ID_PREFIX)
    assert payload[LABEL_FIELD] == DEMO_LABEL
    assert payload[EXPIRES_AT_FIELD].startswith(EXPECTED_EXPIRES_AT_PREFIX)
    assert payload[KEY_FINGERPRINT_FIELD]
    assert payload[KEY_PLAINTEXT_FIELD] == FIXED_SECRET_VALUE

    stored_document = (
        fake_client.collection(typed_create_access_key.DEFAULT_COLLECTION)
        .document(payload[KEY_ID_FIELD])
        .get()
        .to_dict()
    )
    assert stored_document[REVOKED_FIELD] is False


# Verify revoke marks the key as revoked and records who and when.
def test_revoke_by_key_id(capsys: pytest.CaptureFixture[str]) -> None:
    fake_client = FakeClient()
    collection = fake_client.collection(typed_create_access_key.DEFAULT_COLLECTION)
    document_ref = collection.document(REVOKE_KEY_ID)
    document_ref.set({REVOKED_FIELD: False, LABEL_FIELD: DEMO_LABEL})

    current_time = REVOKE_TIMESTAMP
    revoke_args = make_args_for_command(
        COMMAND_REVOKE,
        key_id=REVOKE_KEY_ID,
        revoked_by=REVOKED_BY_USER,
    )

    exit_code = typed_create_access_key.run_revoke(
        revoke_args,
        client=fake_client,
        now=current_time,
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert REVOKED_OUTPUT_SUBSTRING in captured.out

    updated_document = document_ref.get().to_dict()
    assert updated_document[REVOKED_FIELD] is True
    assert updated_document[REVOKED_BY_FIELD] == REVOKED_BY_USER
    assert updated_document[REVOKED_AT_FIELD] == current_time


# Verify missing keys return a non-zero exit code and a helpful error.
def test_revoke_missing_key_returns_error(capsys: pytest.CaptureFixture[str]) -> None:
    fake_client = FakeClient()
    revoke_args = make_args_for_command(COMMAND_REVOKE, key_id=MISSING_KEY_ID)

    exit_code = typed_create_access_key.run_revoke(revoke_args, client=fake_client)
    assert exit_code == 1
    assert NOT_FOUND_ERROR_SUBSTRING in capsys.readouterr().err
