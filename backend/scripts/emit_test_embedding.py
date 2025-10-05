from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from jobs.pack_and_push import load_backend_env


def _open_datapoints(path: Path):
    if path.suffix.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def _first_vector(path: Path) -> list[float]:
    with _open_datapoints(path) as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            vector = record.get("featureVector")
            if vector:
                return [float(x) for x in vector]
    raise RuntimeError(f"No datapoint with featureVector found in {path}")


def main() -> None:
    datapoints_path = os.getenv("DATAPOINTS_FILE")
    if not datapoints_path:
        raise RuntimeError("DATAPOINTS_FILE must be set to locate datapoints file")

    load_backend_env(
        ["PROJECT_ID", "REGION", "INDEX_ENDPOINT_ID", "DEPLOYED_INDEX_ID"],
        optional=[],
    )

    path = Path(datapoints_path).expanduser()
    if not path.is_file():
        raise RuntimeError(f"Missing datapoints file: {path}")

    vector = _first_vector(path)
    print(",".join(str(x) for x in vector))


if __name__ == "__main__":
    main()
