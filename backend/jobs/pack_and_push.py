import json, gzip, hashlib, argparse, pathlib, re, os
from jsonschema import Draft202012Validator
import yaml

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--input", default="../../private/data/chunks.jsonl")
    args = ap.parse_args()

    cfg: dict[str, object] = {}
    if args.settings:
        with open(args.settings, "r", encoding="utf-8") as fh:
            cfg_raw = yaml.safe_load(fh)
        if isinstance(cfg_raw, dict):
            cfg = cfg_raw
        elif cfg_raw is not None:
            raise ValueError("settings file must contain a YAML mapping")
    schema = json.load(open(args.schema))

    records = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            if "id" not in obj:
                obj["id"] = f"cv:auto:{deterministic_id(obj.get('text',''))}"
            Draft202012Validator(schema).validate(obj)
            chunks = split_sentences(obj["text"], 2200)
            if len(chunks) == 1:
                records.append({"id": obj["id"], "text": obj["text"], "metadata": obj.get("metadata", {})})
            else:
                for i, ch in enumerate(chunks):
                    rid = f"{obj['id']}:{i:02d}"
                    records.append({"id": rid, "text": ch, "metadata": obj.get("metadata", {})})

    data = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode("utf-8")
    sha = hashlib.sha1(data).hexdigest()[:12]
    out_name = f"chunks-{sha}.jsonl.gz"
    out_path = pathlib.Path(out_name)
    with gzip.open(out_path, "wb") as gz:
        gz.write(data)

    bucket = (cfg.get("bucket") if cfg else None) or os.getenv("BUCKET_NAME", "")
    if bucket:
        uri = f"gs://{bucket}/{out_name}"
    else:
        uri = f"file://{out_path.resolve()}"
    print(uri)

if __name__ == "__main__":
    main()
