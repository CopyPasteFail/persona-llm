import argparse
import gzip
import hashlib
import json
import os
import re
from pathlib import Path

from dotenv import dotenv_values
from jsonschema import Draft202012Validator


def resolve_existing_path(path_value: str, *roots: Path) -> Path:
    """Resolve a file path against several candidate roots."""
    candidate = Path(path_value).expanduser()

    search_paths: list[Path] = []
    if candidate.is_absolute():
        search_paths.append(candidate)
    else:
        search_paths.append((Path.cwd() / candidate).resolve())
        for root in roots:
            search_paths.append((root / candidate).resolve())
            if candidate.parts and candidate.parts[0] == root.name:
                search_paths.append((root / Path(*candidate.parts[1:])).resolve())

    for path in search_paths:
        if path.exists():
            return path

    raise FileNotFoundError(f"File not found: {candidate}")


def load_backend_env(keys: list[str]) -> dict[str, str]:
    """Load selected backend secrets from the private directory."""
    private_dir = os.getenv("PRIVATE_DIR")
    if not private_dir:
        raise RuntimeError("PRIVATE_DIR is not set. It must point to the private folder.")

    env_path = Path(private_dir).expanduser().resolve() / "secrets" / "backend.env"
    if not env_path.exists():
        raise RuntimeError(f"Missing secrets file: {env_path}")

    env_values = {k: v for k, v in dotenv_values(env_path).items() if v}
    selected: dict[str, str] = {}
    for key in keys:
        value = os.getenv(key) or env_values.get(key)
        if not value:
            raise RuntimeError(f"Missing required env var: {key}")
        selected[key] = value
    return selected

def split_sentences(text: str, max_chars: int = 2200) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= max_chars:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                out.append(cur)
            cur = s
    if cur:
        out.append(cur)
    return out

def deterministic_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

def main():
    backend_root = Path(__file__).resolve().parent.parent
    repo_root = backend_root.parent
    env = load_backend_env(["BUCKET_NAME"])

    default_schema = backend_root / "schema" / "chunk.schema.json"
    default_input = repo_root / "private" / "persona" / "data" / "chunks.jsonl"

    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default=str(default_schema))
    ap.add_argument("--input", default=str(default_input))
    args = ap.parse_args()

    schema_path = resolve_existing_path(args.schema, backend_root)
    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    records = []
    input_path = resolve_existing_path(args.input, repo_root, backend_root)
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            Draft202012Validator(schema).validate(obj)

            base_id = obj.get("id") or f"cv:auto:{deterministic_id(obj.get('text', ''))}"
            metadata = obj.get("metadata", {})

            chunks = split_sentences(obj["text"], 2200)
            if len(chunks) == 1:
                records.append({"id": base_id, "text": obj["text"], "metadata": metadata})
            else:
                for i, ch in enumerate(chunks):
                    rid = f"{base_id}:{i:02d}"
                    records.append({"id": rid, "text": ch, "metadata": metadata})

    data = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode("utf-8")
    sha = hashlib.sha1(data).hexdigest()[:12]
    out_name = f"chunks-{sha}.jsonl.gz"
    out_path = Path(out_name)
    with gzip.open(out_path, "wb") as gz:
        gz.write(data)

    bucket = env["BUCKET_NAME"]
    uri = f"gs://{bucket}/{out_name}"
    print(uri)

if __name__ == "__main__":
    main()
