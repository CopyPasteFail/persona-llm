from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Iterable, Protocol, TextIO

from jobs.pack_and_push import load_backend_env

DATAPOINTS_FILE_ENV_VAR = "DATAPOINTS_FILE"
REQUIRED_BACKEND_ENV_VARS = (
    "PROJECT_ID",
    "REGION",
    "INDEX_ENDPOINT_ID",
    "DEPLOYED_INDEX_ID",
)


class BackendEnvLoader(Protocol):
    """Callable signature for loading backend environment variables."""

    def __call__(
        self, keys: list[str], *, optional: Iterable[str] | None = None
    ) -> dict[str, str]:
        """Load required and optional environment variables."""
        ...


def _open_datapoints(path: Path) -> TextIO:
    """Open a datapoints file as a text stream.

    Args:
        path: Filesystem path to the datapoints file, optionally gzipped.

    Returns:
        A text stream for reading datapoints line-by-line.

    Raises:
        FileNotFoundError: If the path does not exist.
        OSError: If the file cannot be opened.
    """
    if path.suffix.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def _first_vector(path: Path) -> list[float]:
    """Load the first feature vector from a datapoints file.

    Args:
        path: Filesystem path to the datapoints file, optionally gzipped.

    Returns:
        A list of floats representing the first feature vector found.

    Raises:
        RuntimeError: If no datapoint with a feature vector exists.
        json.JSONDecodeError: If a datapoint line is not valid JSON.
    """
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


def _validate_required_env_vars(load_env: BackendEnvLoader) -> None:
    """Ensure required backend environment variables are present.

    Args:
        load_env: Callable that loads/validates required environment variables.

    Raises:
        RuntimeError: If required environment variables are missing.
    """
    load_env(list(REQUIRED_BACKEND_ENV_VARS))


def main(load_env: BackendEnvLoader = load_backend_env) -> None:
    """Emit a comma-separated feature vector for quick manual testing.

    Args:
        load_env: Callable that loads/validates required environment variables.

    Raises:
        RuntimeError: If the datapoints file path is missing or invalid.
    """
    datapoints_path = os.getenv(DATAPOINTS_FILE_ENV_VAR)
    if not datapoints_path:
        raise RuntimeError(
            f"{DATAPOINTS_FILE_ENV_VAR} must be set to locate datapoints file"
        )

    _validate_required_env_vars(load_env)

    path = Path(datapoints_path).expanduser()
    if not path.is_file():
        raise RuntimeError(f"Missing datapoints file: {path}")

    vector = _first_vector(path)
    print(",".join(str(x) for x in vector))


if __name__ == "__main__":
    main()
