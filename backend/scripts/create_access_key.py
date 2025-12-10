from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import sys

from google.cloud import firestore

# Ensure local imports resolve when run as a script from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import keys  # noqa: E402
DEFAULT_COLLECTION = "access_keys"


def _parse_expires_in(raw: str) -> timedelta:
    value = (raw or "").strip().lower()
    if not value:
        raise ValueError("expires-in is required when expires-at is not provided")
    if value.endswith("d"):
        days = int(value[:-1])
        if days <= 0:
            raise ValueError("expires-in days must be positive")
        return timedelta(days=days)
    if value.endswith("h"):
        hours = int(value[:-1])
        if hours <= 0:
            raise ValueError("expires-in hours must be positive")
        return timedelta(hours=hours)
    seconds = int(value)
    if seconds <= 0:
        raise ValueError("expires-in seconds must be positive")
    return timedelta(seconds=seconds)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admin CLI for Firestore-backed access keys.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project",
        help="Firestore project ID. Defaults to PROJECT_ID env var or ADC project.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    create_cmd = sub.add_parser("create", parents=[common], help="Create a new access key")
    create_cmd.add_argument("--label", help="Optional label to store with the key")
    create_cmd.add_argument(
        "--expires-in",
        default="7d",
        help="Expiry window (e.g. 1d, 12h, or seconds). Default: 7d",
    )
    create_cmd.add_argument(
        "--expires-at",
        help="Explicit UTC expiry timestamp (ISO8601). If set, --expires-in is ignored.",
    )
    create_cmd.add_argument("--max-uses", type=int, help="Optional maximum number of uses before lockout")
    create_cmd.add_argument(
        "--print-json",
        action="store_true",
        help="Emit machine-readable JSON (no extra text) with the key material.",
    )

    revoke_cmd = sub.add_parser("revoke", parents=[common], help="Revoke an access key by ID")
    revoke_cmd.add_argument("--key-id", required=True, help="Existing access key document ID")
    revoke_cmd.add_argument("--revoked-by", help="Optional actor to record with the revocation")

    return parser


def _determine_expiry(args: argparse.Namespace, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if args.expires_at:
        ts = args.expires_at
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        if dt <= now:
            raise ValueError("expires-at must be in the future")
        return dt

    delta = _parse_expires_in(args.expires_in)
    return now + delta


def _make_client(project: str | None):
    client_kwargs = {}
    if project:
        client_kwargs["project"] = project
    elif os.getenv("PROJECT_ID"):
        client_kwargs["project"] = os.getenv("PROJECT_ID")
    return firestore.Client(**client_kwargs)


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run_create(args: argparse.Namespace, *, client=None, now: datetime | None = None) -> int:
    expires_at = _determine_expiry(args, now=now)

    plain_key = secrets.token_urlsafe(24)
    key_hash = keys.hash_key(plain_key)
    fingerprint = keys.compute_key_fingerprint(plain_key)

    client = client or _make_client(args.project)
    collection = client.collection(DEFAULT_COLLECTION)
    doc_ref = collection.document()

    created_at = now or datetime.now(timezone.utc)
    doc = {
        "key_hash": key_hash,
        "key_fingerprint": fingerprint,
        "expires_at": expires_at,
        "revoked": False,
        "label": args.label,
        "created_at": created_at,
        "created_by": os.getenv("USER") or os.getenv("LOGNAME"),
        "used_count": 0,
    }
    if args.max_uses is not None:
        if args.max_uses <= 0:
            raise ValueError("max-uses must be greater than zero")
        doc["max_uses"] = int(args.max_uses)

    doc_ref.set({k: v for k, v in doc.items() if v is not None})

    if args.print_json:
        output = {
            "key_id": doc_ref.id,
            "label": args.label,
            "expires_at": _isoformat(expires_at),
            "key_fingerprint": fingerprint,
            "key_plaintext": plain_key,
        }
        if doc.get("max_uses") is not None:
            output["max_uses"] = doc["max_uses"]
        print(json.dumps(output))
    else:
        print("Access key created:")
        print(f"  label={doc.get('label') or '-'}")
        print(f"  expires_at={expires_at.isoformat()}")
        print(f"  key_id={doc_ref.id}")
        print(f"  KEY={plain_key}")
    return 0


def run_revoke(args: argparse.Namespace, *, client=None, now: datetime | None = None) -> int:
    client = client or _make_client(args.project)
    collection = client.collection(DEFAULT_COLLECTION)
    doc_ref = collection.document(args.key_id)
    snapshot = doc_ref.get()
    if not getattr(snapshot, "exists", False):
        print(f"Key {args.key_id} not found", file=sys.stderr)
        return 1

    update = {
        "revoked": True,
        "revoked_at": (now or datetime.now(timezone.utc)),
    }
    if args.revoked_by:
        update["revoked_by"] = args.revoked_by
    doc_ref.update(update)
    print(f"Key {args.key_id} revoked")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "create":
        return run_create(args)
    if args.command == "revoke":
        return run_revoke(args)
    parser.error("Command is required")


if __name__ == "__main__":
    raise SystemExit(main())
