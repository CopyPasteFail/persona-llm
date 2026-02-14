"""Offline tests for pack_and_push data processing and artifact metadata behavior.

Scope:
- Sentence splitting and deterministic identifiers
- Schema-validated chunk loading and record building
- Serialization, manifest writing, and path resolution
- Upload logic via a mocked storage client (no network)
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from jsonschema import ValidationError

from jobs import pack_and_push

MAX_CHARS_LIMIT = 32
EXPECTED_ID_LENGTH = 12
EXPECTED_FRAGMENT_COUNT = 2
SCHEMA_VERSION = 2
PROFILE_INFRA = "infra"
TOPIC_LABEL = "infra"
TAG_LABEL = "role:infra"
PERMISSION_LABEL = "internal"
DOC_ID_VALUE = "doc-1"
CHUNK_ID_VALUE = "chunk-1"
TEXT_SHORT = "Alpha one."
TEXT_LONG = "Alpha one. Beta two. Gamma three. Delta four."
TEXT_WITH_TWO_SENTENCES = "Alpha one is longer. Beta two is longer."
SECTION_LABEL = "overview"
LANGUAGE_CODE = "en"
UPDATED_AT_VALUE = "2024-01-01T00:00:00Z"
SOURCE_URI_VALUE = "https://example.com/source"
EXTRA_EMPLOYER = "Persona"
EXTRA_TYPE = "achievement"
START_YEAR_VALUE = 2020
END_YEAR_VALUE = 2024
FILE_CONTENT_BYTES = b"sample-content"
JSONL_FILENAME = "chunks.jsonl"
SCHEMA_FILENAME = "chunk.schema.json"
BUCKET_NAME = "example-bucket"
OBJECT_NAME = "artifact.jsonl.gz"
EXPECTED_MANIFEST_SUFFIX = ".manifest.json"
EXPECTED_GZ_EXTENSION = ".jsonl.gz"
EXPECTED_CHUNKS_PREFIX = "chunks-"
EXPECTED_Z_SUFFIX = "Z"
ARTIFACT_FILENAME = "artifact.jsonl.gz"
PAYLOAD_FILENAME = "payload.bin"
RESOLVE_FILENAME = "data.txt"
ARTIFACT_URI = "gs://example/artifact.jsonl.gz"


def write_schema_file(schema_path: Path) -> None:
    """Guarantee a minimal chunk schema is present at the given path."""
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PersonaChunkTest",
        "type": "object",
        "required": ["doc_id", "chunk_id", "position", "text", "profile"],
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
            "doc_id": {"type": "string", "minLength": 1},
            "chunk_id": {"type": "string", "minLength": 1},
            "position": {"type": "integer", "minimum": 1},
            "text": {"type": "string", "minLength": 1},
            "profile": {"type": "string", "minLength": 1},
            "topics": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "section": {"type": "string", "minLength": 1},
            "start_year": {"type": "integer"},
            "end_year": {"type": "integer"},
            "lang": {"type": "string", "minLength": 2},
            "updated_at": {"type": "string", "format": "date-time"},
            "source_uri": {"type": "string", "format": "uri"},
            "permissions": {"type": "array", "items": {"type": "string"}},
            "extras": {"type": "object"},
        },
        "additionalProperties": False,
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")


def write_chunks_file(chunks_path: Path, chunks: list[dict[str, object]]) -> None:
    """Guarantee the chunks list is persisted as JSONL at the given path."""
    jsonl_content = "\n".join(json.dumps(chunk) for chunk in chunks)
    chunks_path.write_text(jsonl_content + "\n", encoding="utf-8")


def test_split_sentences_returns_single_fragment_when_under_limit() -> None:
    """Verify short text is returned as a single fragment without modification.

    What is tested:
        The sentence splitter should not alter text when its length is already
        within the configured maximum.
    How it's tested:
        Call split_sentences with TEXT_SHORT and a max_chars value larger than
        the input length.
    Expected result format:
        The returned list contains exactly one element equal to TEXT_SHORT.
    """
    # Arrange
    max_chars_limit = MAX_CHARS_LIMIT

    # Act
    fragments = pack_and_push.split_sentences(TEXT_SHORT, max_chars=max_chars_limit)

    # Assert
    assert fragments == [TEXT_SHORT]


def test_split_sentences_splits_on_sentence_boundaries_when_over_limit() -> None:
    """Verify long text is split into multiple fragments within size limits.

    What is tested:
        The sentence splitter should break long input into multiple pieces
        while respecting the max_chars bound.
    How it's tested:
        Call split_sentences with TEXT_LONG and a small max_chars limit.
    Expected result format:
        The result has more than one fragment and every fragment length is
        less than or equal to max_chars.
    """
    # Arrange
    max_chars_limit = MAX_CHARS_LIMIT

    # Act
    fragments = pack_and_push.split_sentences(TEXT_LONG, max_chars=max_chars_limit)

    # Assert
    assert len(fragments) > 1
    assert all(len(fragment) <= max_chars_limit for fragment in fragments)


def test_deterministic_id_returns_stable_hex_fragment() -> None:
    """Verify deterministic_id returns stable 12-char lowercase hex output.

    What is tested:
        Deterministic IDs should be reproducible and have the expected format.
    How it's tested:
        Call deterministic_id twice with the same input string.
    Expected result format:
        Both outputs match, have length EXPECTED_ID_LENGTH, and match the
        hex-only regex pattern.
    """
    # Arrange
    input_text = TEXT_SHORT

    # Act
    first_identifier = pack_and_push.deterministic_id(input_text)
    second_identifier = pack_and_push.deterministic_id(input_text)

    # Assert
    assert first_identifier == second_identifier
    assert len(first_identifier) == EXPECTED_ID_LENGTH
    assert re.fullmatch(r"[0-9a-f]{12}", first_identifier)


def test_build_metadata_includes_expected_fields_and_copies_collections() -> None:
    """Verify metadata includes expected fields and copies list values.

    What is tested:
        _build_metadata should carry over key scalar fields and copy list-based
        fields so mutations do not leak back to the source chunk.
    How it's tested:
        Build a chunk dict with all expected fields, call _build_metadata, and
        compare the output against known values.
    Expected result format:
        Metadata contains the expected scalar fields and list contents, plus
        fragment_index/fragment_count, and list values are new lists.
    """
    # Arrange
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": PROFILE_INFRA,
        "section": SECTION_LABEL,
        "start_year": START_YEAR_VALUE,
        "end_year": END_YEAR_VALUE,
        "lang": LANGUAGE_CODE,
        "updated_at": UPDATED_AT_VALUE,
        "source_uri": SOURCE_URI_VALUE,
        "topics": [TOPIC_LABEL],
        "tags": [TAG_LABEL],
        "permissions": [PERMISSION_LABEL],
        "extras": {"employer": EXTRA_EMPLOYER, "type": EXTRA_TYPE},
    }

    # Act
    metadata = pack_and_push._build_metadata(  # pyright: ignore[reportPrivateUsage]
        chunk,
        index=0,
        total=1,
    )

    # Assert
    assert metadata["doc_id"] == DOC_ID_VALUE
    assert metadata["chunk_id"] == CHUNK_ID_VALUE
    assert metadata["position"] == 1
    assert metadata["profile"] == PROFILE_INFRA
    assert metadata["section"] == SECTION_LABEL
    assert metadata["start_year"] == START_YEAR_VALUE
    assert metadata["end_year"] == END_YEAR_VALUE
    assert metadata["lang"] == LANGUAGE_CODE
    assert metadata["updated_at"] == UPDATED_AT_VALUE
    assert metadata["source_uri"] == SOURCE_URI_VALUE
    assert metadata["topics"] == [TOPIC_LABEL]
    assert metadata["tags"] == [TAG_LABEL]
    assert metadata["permissions"] == [PERMISSION_LABEL]
    assert metadata["extras"] == {"employer": EXTRA_EMPLOYER, "type": EXTRA_TYPE}
    assert metadata["fragment_index"] == 0
    assert metadata["fragment_count"] == 1
    assert metadata["topics"] is not chunk["topics"]
    assert metadata["tags"] is not chunk["tags"]
    assert metadata["permissions"] is not chunk["permissions"]


def test_build_metadata_sets_profile_when_source_chunk_has_profile_only() -> None:
    """Verify profile-only chunks populate canonical metadata profile."""
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": PROFILE_INFRA,
    }

    metadata = pack_and_push._build_metadata(  # pyright: ignore[reportPrivateUsage]
        chunk,
        index=0,
        total=1,
    )

    assert metadata["profile"] == PROFILE_INFRA


def test_load_chunks_raises_on_schema_violation(tmp_path: Path) -> None:
    """Verify schema violations raise ValidationError during chunk loading.

    What is tested:
        _load_chunks should validate each JSONL line against the provided schema.
    How it's tested:
        Write a schema file, then a JSONL file containing one valid chunk and
        one invalid chunk (missing a required field), and iterate _load_chunks.
    Expected result format:
        A ValidationError is raised when the invalid chunk is processed.
    """
    # Arrange
    schema_path = tmp_path / SCHEMA_FILENAME
    write_schema_file(schema_path)
    valid_chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": PROFILE_INFRA,
    }
    invalid_chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "profile": PROFILE_INFRA,
    }
    chunks_path = tmp_path / JSONL_FILENAME
    write_chunks_file(chunks_path, [valid_chunk, invalid_chunk])
    schema: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))

    # Act / Assert
    with pytest.raises(ValidationError):
        list(
            pack_and_push._load_chunks(  # pyright: ignore[reportPrivateUsage]
                chunks_path,
                schema,
            )
        )


def test_build_persona_records_splits_text_and_sets_fragment_metadata(
    tmp_path: Path,
) -> None:
    """Verify build_persona_records splits text and annotates fragments.

    What is tested:
        build_persona_records should split long text into multiple records and
        set fragment metadata consistently.
    How it's tested:
        Write a schema and a JSONL file with a two-sentence text, then call
        build_persona_records with a small max_chars value.
    Expected result format:
        Multiple records are returned, record IDs start with the base chunk ID,
        and fragment_index/fragment_count are set appropriately.
    """
    # Arrange
    schema_path = tmp_path / SCHEMA_FILENAME
    write_schema_file(schema_path)
    chunks_path = tmp_path / JSONL_FILENAME
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_WITH_TWO_SENTENCES,
        "profile": PROFILE_INFRA,
    }
    write_chunks_file(chunks_path, [chunk])

    # Act
    records: list[dict[str, Any]] = pack_and_push.build_persona_records(
        schema_path,
        chunks_path,
        max_chars=MAX_CHARS_LIMIT,
    )

    # Assert
    assert len(records) == EXPECTED_FRAGMENT_COUNT
    assert records[0]["id"].startswith(CHUNK_ID_VALUE)
    assert records[0]["metadata"]["fragment_count"] == EXPECTED_FRAGMENT_COUNT
    assert records[1]["metadata"]["fragment_index"] == 1


def test_load_chunks_canonicalizes_profile_and_stint_domains(tmp_path: Path) -> None:
    """Verify ingestion canonicalizes profile and extras.stint_domains values."""
    schema_path = tmp_path / SCHEMA_FILENAME
    write_schema_file(schema_path)
    chunks_path = tmp_path / JSONL_FILENAME
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": "InFra",
        "section": "Experience",
        "extras": {
            "employer": "Acme",
            "title": "SRE",
            "stint_domains": ["SRE", "devops", "sre"],
        },
    }
    write_chunks_file(chunks_path, [chunk])
    schema: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))

    loaded_chunks = list(
        pack_and_push._load_chunks(chunks_path, schema)  # pyright: ignore[reportPrivateUsage]
    )

    assert loaded_chunks[0]["profile"] == "infra"
    assert loaded_chunks[0]["extras"]["stint_domains"] == ["devops", "sre"]


def test_load_chunks_rejects_unknown_stint_domains_with_context(tmp_path: Path) -> None:
    """Unknown extras.stint_domains values should fail with doc/chunk context."""
    schema_path = tmp_path / SCHEMA_FILENAME
    write_schema_file(schema_path)
    chunks_path = tmp_path / JSONL_FILENAME
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": PROFILE_INFRA,
        "section": "Experience",
        "extras": {
            "employer": "Acme",
            "title": "Engineer",
            "stint_domains": ["unknown_label"],
        },
    }
    write_chunks_file(chunks_path, [chunk])
    schema: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))

    with pytest.raises(ValueError) as error_info:
        list(
            pack_and_push._load_chunks(  # pyright: ignore[reportPrivateUsage]
                chunks_path,
                schema,
            )
        )

    assert DOC_ID_VALUE in str(error_info.value)
    assert CHUNK_ID_VALUE in str(error_info.value)


def test_load_chunks_rejects_experience_missing_title_with_context(tmp_path: Path) -> None:
    """Experience chunks without extras.title should fail with clear context."""
    schema_path = tmp_path / SCHEMA_FILENAME
    write_schema_file(schema_path)
    chunks_path = tmp_path / JSONL_FILENAME
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": PROFILE_INFRA,
        "section": "Experience",
        "extras": {
            "employer": "Acme",
            "stint_domains": ["devops"],
        },
    }
    write_chunks_file(chunks_path, [chunk])
    schema: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))

    with pytest.raises(ValueError) as error_info:
        list(
            pack_and_push._load_chunks(  # pyright: ignore[reportPrivateUsage]
                chunks_path,
                schema,
            )
        )

    assert "extras.title" in str(error_info.value)
    assert DOC_ID_VALUE in str(error_info.value)
    assert CHUNK_ID_VALUE in str(error_info.value)


def test_load_chunks_allows_non_experience_chunks_without_stint_requirements(
    tmp_path: Path,
) -> None:
    """Non-Experience chunks should not enforce employer/title/stint_domains validation."""
    schema_path = tmp_path / SCHEMA_FILENAME
    write_schema_file(schema_path)
    chunks_path = tmp_path / JSONL_FILENAME
    chunk: dict[str, Any] = {
        "doc_id": DOC_ID_VALUE,
        "chunk_id": CHUNK_ID_VALUE,
        "position": 1,
        "text": TEXT_SHORT,
        "profile": PROFILE_INFRA,
        "section": "Summary",
        "extras": {
            "stint_domains": ["unknown_label"],
        },
    }
    write_chunks_file(chunks_path, [chunk])
    schema: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))

    loaded_chunks = list(
        pack_and_push._load_chunks(chunks_path, schema)  # pyright: ignore[reportPrivateUsage]
    )

    assert len(loaded_chunks) == 1
    assert loaded_chunks[0]["section"] == "Summary"
    assert loaded_chunks[0]["extras"]["stint_domains"] == ["unknown_label"]


def test_serialize_records_returns_expected_payload_and_filename() -> None:
    """Verify record serialization returns JSONL bytes and a gzip filename.

    What is tested:
        _serialize_records should emit UTF-8 JSONL content and a filename with
        the expected chunks- prefix and .jsonl.gz suffix.
    How it's tested:
        Serialize two minimal records and inspect the returned payload and
        filename.
    Expected result format:
        The filename has the expected prefix/suffix, the payload decodes into
        two JSON lines, and the first line matches the first record ID.
    """
    # Arrange
    records: list[dict[str, Any]] = [
        {"id": "id-1", "text": "Alpha", "metadata": {"fragment_index": 0}},
        {"id": "id-2", "text": "Beta", "metadata": {"fragment_index": 1}},
    ]

    # Act
    payload, filename = pack_and_push._serialize_records(  # pyright: ignore[reportPrivateUsage]
        records
    )

    # Assert
    assert filename.startswith(EXPECTED_CHUNKS_PREFIX)
    assert filename.endswith(EXPECTED_GZ_EXTENSION)
    lines = payload.decode("utf-8").splitlines()
    assert len(lines) == len(records)
    assert json.loads(lines[0])["id"] == "id-1"


def test_sha256_digest_matches_expected_value(tmp_path: Path) -> None:
    """Verify _sha256_digest matches a known SHA-256 hash for a file.

    What is tested:
        The digest helper should compute the same hash as hashlib.sha256 on the
        file's bytes.
    How it's tested:
        Write FILE_CONTENT_BYTES to disk and compare _sha256_digest against a
        locally computed hashlib.sha256 hexdigest.
    Expected result format:
        The computed digest equals the expected_digest string.
    """
    # Arrange
    file_path = tmp_path / PAYLOAD_FILENAME
    file_path.write_bytes(FILE_CONTENT_BYTES)
    expected_digest = hashlib.sha256(FILE_CONTENT_BYTES).hexdigest()

    # Act
    actual_digest = pack_and_push._sha256_digest(  # pyright: ignore[reportPrivateUsage]
        file_path
    )

    # Assert
    assert actual_digest == expected_digest


def test_write_manifest_creates_manifest_with_checksum(tmp_path: Path) -> None:
    """Verify manifest writing includes checksum and artifact metadata.

    What is tested:
        _write_manifest should produce a manifest file that records artifact
        size, checksum, and timestamps.
    How it's tested:
        Create a fake artifact file, minimal schema/input files, then call
        _write_manifest and read the JSON output.
    Expected result format:
        The manifest filename ends with EXPECTED_MANIFEST_SUFFIX, the recorded
        size matches the artifact, the checksum matches SHA-256, and the
        generated_at timestamp ends with "Z".
    """
    # Arrange
    artifact_path = tmp_path / ARTIFACT_FILENAME
    artifact_path.write_bytes(FILE_CONTENT_BYTES)
    schema_path = tmp_path / SCHEMA_FILENAME
    input_path = tmp_path / JSONL_FILENAME
    schema_path.write_text("{}", encoding="utf-8")
    input_path.write_text("{}", encoding="utf-8")

    # Act
    manifest_path = pack_and_push._write_manifest(  # pyright: ignore[reportPrivateUsage]
        artifact_path=artifact_path,
        artifact_uri=ARTIFACT_URI,
        schema_path=schema_path,
        input_path=input_path,
        record_count=1,
    )

    # Assert
    assert manifest_path.name.endswith(EXPECTED_MANIFEST_SUFFIX)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact"]["size_bytes"] == artifact_path.stat().st_size
    assert manifest["artifact"]["checksum"]["value"] == hashlib.sha256(
        FILE_CONTENT_BYTES
    ).hexdigest()
    assert manifest["generated_at"].endswith(EXPECTED_Z_SUFFIX)


def test_resolve_existing_path_returns_existing_candidate(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify resolve_existing_path finds the first existing candidate path.

    What is tested:
        resolve_existing_path should search candidate roots and return the first
        existing file that matches the input name.
    How it's tested:
        Create a file under a root directory, set cwd to tmp_path, then call
        resolve_existing_path with the filename and root directory.
    Expected result format:
        The returned path equals the path to the created file.
    """
    # Arrange
    filename = RESOLVE_FILENAME
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    expected_path = root_dir / filename
    expected_path.write_text("content", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Act
    resolved_path = pack_and_push.resolve_existing_path(filename, root_dir)

    # Assert
    assert resolved_path == expected_path


def test_upload_to_bucket_uses_storage_client_and_returns_uri(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify upload_to_bucket calls the storage client and returns a gs:// URI.

    What is tested:
        upload_to_bucket should call the storage client with the expected bucket
        and object names and return a gs:// URI string.
    How it's tested:
        Monkeypatch storage.Client with a fake client that records bucket, object,
        and upload calls; then invoke upload_to_bucket with a temp file.
    Expected result format:
        The returned URI matches the bucket/object pair, the upload was recorded
        once, and the recorded bucket/object names match the inputs.
    """
    # Arrange
    file_path = tmp_path / ARTIFACT_FILENAME
    file_path.write_bytes(FILE_CONTENT_BYTES)
    uploaded_paths: list[str] = []
    requested_bucket_names: list[str] = []
    requested_object_names: list[str] = []

    class FakeBlob:
        """Fake storage blob that records upload calls."""

        def upload_from_filename(self, filename: str, *, timeout: int | None = None) -> None:
            """Guarantee the uploaded filename is captured for assertions."""
            uploaded_paths.append(filename)

    class FakeBucket:
        """Fake storage bucket that returns a FakeBlob instance."""

        def blob(self, object_name: str) -> FakeBlob:
            """Guarantee the requested object name is recorded."""
            requested_object_names.append(object_name)
            return FakeBlob()

    class FakeClient:
        """Fake storage client that returns a FakeBucket instance."""

        def bucket(self, bucket_name: str) -> FakeBucket:
            """Guarantee the requested bucket name is recorded."""
            requested_bucket_names.append(bucket_name)
            return FakeBucket()

    def fake_client_factory() -> FakeClient:
        """Guarantee a FakeClient instance is returned."""
        return FakeClient()

    monkeypatch.setattr(pack_and_push.storage, "Client", fake_client_factory)

    # Act
    uri = pack_and_push.upload_to_bucket(file_path, BUCKET_NAME, OBJECT_NAME)

    # Assert
    assert uri == f"gs://{BUCKET_NAME}/{OBJECT_NAME}"
    assert uploaded_paths == [str(file_path)]
    assert requested_bucket_names == [BUCKET_NAME]
    assert requested_object_names == [OBJECT_NAME]
