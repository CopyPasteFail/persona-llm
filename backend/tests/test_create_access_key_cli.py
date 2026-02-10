import argparse
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import create_access_key


class FakeSnapshot:
    def __init__(self, data: dict | None = None):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data or {}


class FakeDocRef:
    def __init__(self, collection: "FakeCollection", doc_id: str):
        self._collection = collection
        self.id = doc_id

    def set(self, data: dict):
        self._collection._data[self.id] = dict(data)

    def get(self):
        data = self._collection._data.get(self.id)
        return FakeSnapshot(data)

    def update(self, data: dict):
        if self.id not in self._collection._data:
            raise KeyError("missing document")
        self._collection._data[self.id].update(data)


class FakeCollection:
    def __init__(self):
        self._data: dict[str, dict] = {}
        self._counter = 0

    def document(self, doc_id: str | None = None) -> FakeDocRef:
        if doc_id is None:
            self._counter += 1
            doc_id = f"auto-{self._counter}"
        return FakeDocRef(self, doc_id)


class FakeClient:
    def __init__(self):
        self._collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


def _make_args(command: str, **kwargs):
    defaults = {
        "label": None,
        "expires_in": "7d",
        "expires_at": None,
        "max_uses": None,
        "print_json": False,
        "project": None,
        "key_id": None,
        "revoked_by": None,
    }
    defaults.update(kwargs)
    defaults["command"] = command
    return argparse.Namespace(**defaults)


def test_create_prints_json(monkeypatch, capsys):
    fake_client = FakeClient()
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(create_access_key, "secrets", SimpleNamespace(token_urlsafe=lambda n: "fixed-secret"))

    args = _make_args("create", label="demo", expires_in="1d", print_json=True, max_uses=5)
    code = create_access_key.run_create(args, client=fake_client, now=now)
    assert code == 0

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["key_id"].startswith("auto-")
    assert payload["label"] == "demo"
    assert payload["expires_at"].startswith("2024-01-02T12:00:00")
    assert payload["max_uses"] == 5
    assert payload["key_fingerprint"]
    assert payload["key_plaintext"] == "fixed-secret"

    stored = fake_client.collection(create_access_key.DEFAULT_COLLECTION).document(payload["key_id"]).get().to_dict()
    assert stored["revoked"] is False
    assert stored["max_uses"] == 5


def test_revoke_by_key_id(monkeypatch, capsys):
    fake_client = FakeClient()
    collection = fake_client.collection(create_access_key.DEFAULT_COLLECTION)
    doc = collection.document("abc123")
    doc.set({"revoked": False, "label": "demo"})

    now = datetime(2024, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
    args = _make_args("revoke", key_id="abc123", revoked_by="tester")

    code = create_access_key.run_revoke(args, client=fake_client, now=now)
    assert code == 0
    captured = capsys.readouterr()
    assert "revoked" in captured.out

    updated = doc.get().to_dict()
    assert updated["revoked"] is True
    assert updated["revoked_by"] == "tester"
    assert updated["revoked_at"] == now


def test_revoke_missing_key_returns_error(capsys):
    fake_client = FakeClient()
    args = _make_args("revoke", key_id="missing")

    code = create_access_key.run_revoke(args, client=fake_client)
    assert code == 1
    assert "not found" in capsys.readouterr().err
