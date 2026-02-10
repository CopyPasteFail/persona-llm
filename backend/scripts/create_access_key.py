from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Protocol, cast

from google.cloud import firestore  # type: ignore[import]

# Ensure local imports resolve when run as a script from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import keys

class DocumentSnapshotProtocol(Protocol):
    """Firestore document snapshot surface needed by this script."""

    @property
    def exists(self) -> bool:
        ...


class DocumentReferenceProtocol(Protocol):
    """Firestore document reference surface needed by this script."""

    @property
    def id(self) -> str:
        ...

    def get(self) -> DocumentSnapshotProtocol:
        ...

    def set(self, document_data: dict[str, object]) -> Any:
        ...

    def update(self, field_updates: dict[str, object]) -> Any:
        ...


class CollectionReferenceProtocol(Protocol):
    """Firestore collection reference surface needed by this script."""

    def document(self, document_id: str | None = None) -> DocumentReferenceProtocol:
        ...


class FirestoreClientProtocol(Protocol):
    """Firestore client surface needed by this script."""

    def collection(self, collection_path: str) -> CollectionReferenceProtocol:
        ...

COMMAND_CREATE = "create"
COMMAND_REVOKE = "revoke"
DEFAULT_COLLECTION = "access_keys"
DEFAULT_EXPIRES_IN = "7d"
ENV_PROJECT_ID = "PROJECT_ID"
ENV_USER = "USER"
ENV_LOGNAME = "LOGNAME"
KEY_BYTES_URLSAFE = 24

DOC_FIELD_CREATED_AT = "created_at"
DOC_FIELD_CREATED_BY = "created_by"
DOC_FIELD_EXPIRES_AT = "expires_at"
DOC_FIELD_KEY_FINGERPRINT = "key_fingerprint"
DOC_FIELD_KEY_HASH = "key_hash"
DOC_FIELD_LABEL = "label"
DOC_FIELD_REVOKED = "revoked"
DOC_FIELD_REVOKED_AT = "revoked_at"
DOC_FIELD_REVOKED_BY = "revoked_by"

OUTPUT_KEY_EXPIRES_AT = "expires_at"
OUTPUT_KEY_ID = "key_id"
OUTPUT_KEY_FINGERPRINT = "key_fingerprint"
OUTPUT_KEY_LABEL = "label"
OUTPUT_KEY_PLAINTEXT = "key_plaintext"

ERROR_EXPIRES_IN_REQUIRED = "expires-in is required when expires-at is not provided"
ERROR_EXPIRES_IN_DAYS_POSITIVE = "expires-in days must be positive"
ERROR_EXPIRES_IN_HOURS_POSITIVE = "expires-in hours must be positive"
ERROR_EXPIRES_IN_SECONDS_POSITIVE = "expires-in seconds must be positive"
ERROR_EXPIRES_AT_FUTURE = "expires-at must be in the future"
ERROR_KEY_NOT_FOUND = "Key {key_id} not found"
ERROR_COMMAND_REQUIRED = "Command is required"


def _parse_expires_in(raw: str) -> timedelta:
    """Parse an expiry offset string into a timedelta.

    Args:
        raw: A value like "7d", "12h", or "3600".

    Returns:
        Timedelta representing the expiry offset.

    Raises:
        ValueError: When the value is missing or non-positive.
    """
    value = (raw or "").strip().lower()
    if not value:
        raise ValueError(ERROR_EXPIRES_IN_REQUIRED)
    if value.endswith("d"):
        days = int(value[:-1])
        if days <= 0:
            raise ValueError(ERROR_EXPIRES_IN_DAYS_POSITIVE)
        return timedelta(days=days)
    if value.endswith("h"):
        hours = int(value[:-1])
        if hours <= 0:
            raise ValueError(ERROR_EXPIRES_IN_HOURS_POSITIVE)
        return timedelta(hours=hours)
    seconds = int(value)
    if seconds <= 0:
        raise ValueError(ERROR_EXPIRES_IN_SECONDS_POSITIVE)
    return timedelta(seconds=seconds)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for access key management commands.

    Returns:
        Configured ArgumentParser for the CLI.
    """
    parser = argparse.ArgumentParser(description="Admin CLI for Firestore-backed access keys.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project",
        help="Firestore project ID. Defaults to PROJECT_ID env var or ADC project.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    create_cmd = sub.add_parser(COMMAND_CREATE, parents=[common], help="Create a new access key")
    create_cmd.add_argument("--label", help="Optional label to store with the key")
    create_cmd.add_argument(
        "--expires-in",
        default=DEFAULT_EXPIRES_IN,
        help="Expiry window (e.g. 1d, 12h, or seconds). Default: 7d",
    )
    create_cmd.add_argument(
        "--expires-at",
        help="Explicit UTC expiry timestamp (ISO8601). If set, --expires-in is ignored.",
    )
    create_cmd.add_argument(
        "--print-json",
        action="store_true",
        help="Emit machine-readable JSON (no extra text) with the key material.",
    )

    revoke_cmd = sub.add_parser(COMMAND_REVOKE, parents=[common], help="Revoke an access key by ID")
    revoke_cmd.add_argument("--key-id", required=True, help="Existing access key document ID")
    revoke_cmd.add_argument("--revoked-by", help="Optional actor to record with the revocation")

    return parser


def _determine_expiry(args: argparse.Namespace, *, now: datetime | None = None) -> datetime:
    """Resolve the expiry timestamp from CLI arguments.

    Args:
        args: Parsed CLI arguments.
        now: Optional timestamp used for deterministic testing.

    Returns:
        UTC datetime for the access key expiry.

    Raises:
        ValueError: When the expiry would be in the past.
    """
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
            raise ValueError(ERROR_EXPIRES_AT_FUTURE)
        return dt

    delta = _parse_expires_in(args.expires_in)
    return now + delta


def _make_client(project: str | None) -> FirestoreClientProtocol:
    """Create a Firestore client using explicit or environment configuration.

    Args:
        project: Optional Firestore project ID override.

    Returns:
        Firestore client instance.
    """
    project_id = project or os.getenv(ENV_PROJECT_ID)
    if project_id:
        client = firestore.Client(project=project_id)
    else:
        client = firestore.Client()
    return cast(FirestoreClientProtocol, client)


def _isoformat(dt: datetime) -> str:
    """Format datetimes as UTC ISO8601 with Z suffix."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run_create(
    args: argparse.Namespace,
    *,
    client: FirestoreClientProtocol | None = None,
    now: datetime | None = None,
) -> int:
    """Create a Firestore-backed access key and print the result.

    Args:
        args: Parsed CLI arguments.
        client: Optional Firestore client for dependency injection.
        now: Optional timestamp for deterministic testing.

    Returns:
        Process exit code (0 for success).
    """
    expires_at = _determine_expiry(args, now=now)

    plain_key = secrets.token_urlsafe(KEY_BYTES_URLSAFE)
    key_hash = keys.hash_key(plain_key)
    fingerprint = keys.compute_key_fingerprint(plain_key)

    client = client or _make_client(args.project)
    collection: CollectionReferenceProtocol = client.collection(DEFAULT_COLLECTION)
    doc_ref: DocumentReferenceProtocol = collection.document()

    created_at = now or datetime.now(timezone.utc)
    doc: dict[str, object] = {
        DOC_FIELD_KEY_HASH: key_hash,
        DOC_FIELD_KEY_FINGERPRINT: fingerprint,
        DOC_FIELD_EXPIRES_AT: expires_at,
        DOC_FIELD_REVOKED: False,
        DOC_FIELD_LABEL: args.label,
        DOC_FIELD_CREATED_AT: created_at,
        DOC_FIELD_CREATED_BY: os.getenv(ENV_USER) or os.getenv(ENV_LOGNAME),
    }

    write_doc: dict[str, object] = {k: v for k, v in doc.items() if v is not None}
    doc_ref.set(write_doc)

    if args.print_json:
        output: dict[str, str | None] = {
            OUTPUT_KEY_ID: doc_ref.id,
            OUTPUT_KEY_LABEL: args.label,
            OUTPUT_KEY_EXPIRES_AT: _isoformat(expires_at),
            OUTPUT_KEY_FINGERPRINT: fingerprint,
            OUTPUT_KEY_PLAINTEXT: plain_key,
        }
        print(json.dumps(output))
    else:
        print("Access key created:")
        print(f"  label={doc.get(DOC_FIELD_LABEL) or '-'}")
        print(f"  expires_at={expires_at.isoformat()}")
        print(f"  key_id={doc_ref.id}")
        print(f"  KEY={plain_key}")
    return 0


def run_revoke(
    args: argparse.Namespace,
    *,
    client: FirestoreClientProtocol | None = None,
    now: datetime | None = None,
) -> int:
    """Revoke an existing access key by its document ID.

    Args:
        args: Parsed CLI arguments.
        client: Optional Firestore client for dependency injection.
        now: Optional timestamp for deterministic testing.

    Returns:
        Process exit code (0 for success, 1 if the key does not exist).
    """
    client = client or _make_client(args.project)
    collection: CollectionReferenceProtocol = client.collection(DEFAULT_COLLECTION)
    doc_ref: DocumentReferenceProtocol = collection.document(args.key_id)
    snapshot: DocumentSnapshotProtocol = doc_ref.get()
    if not getattr(snapshot, "exists", False):
        print(ERROR_KEY_NOT_FOUND.format(key_id=args.key_id), file=sys.stderr)
        return 1

    update: dict[str, object] = {
        DOC_FIELD_REVOKED: True,
        DOC_FIELD_REVOKED_AT: (now or datetime.now(timezone.utc)),
    }
    if args.revoked_by:
        update[DOC_FIELD_REVOKED_BY] = args.revoked_by
    doc_ref.update(update)
    print(f"Key {args.key_id} revoked")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for access key creation and revocation.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == COMMAND_CREATE:
        return run_create(args)
    if args.command == COMMAND_REVOKE:
        return run_revoke(args)
    parser.error(ERROR_COMMAND_REQUIRED)


if __name__ == "__main__":
    raise SystemExit(main())
