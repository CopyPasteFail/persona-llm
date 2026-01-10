"""Offline tests for load_backend_env configuration loading behavior.

Scope:
- Required vs optional environment keys
- Merging common.env with backend.env
- Environment variable expansion ($VARS, ~)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from jobs.pack_and_push import load_backend_env

ENV_DIR_NAME = "secrets"
BACKEND_ENV_FILENAME = "backend.env"
COMMON_ENV_FILENAME = "common.env"
PRIVATE_DIR_ENV_VAR = "PRIVATE_DIR"
HOME_ENV_VAR = "HOME"
REQUIRED_BUCKET_KEY = "BUCKET_NAME"
OPTIONAL_CREDENTIALS_KEY = "GOOGLE_APPLICATION_CREDENTIALS"
OVERRIDDEN_VALUE = "backend-bucket"
COMMON_VALUE = "common-bucket"
HOME_SUBPATH = "home/dir"
EXPANDED_HOME_VALUE = "expanded/home/dir"
ENV_VALUE_KEY = "ENV_ONLY_KEY"
ENV_VALUE_VALUE = "from-env"
OTHER_ENV_KEY = "OTHER_KEY"


def create_secrets_dir(private_dir: Path) -> Path:
    """Ensure a secrets directory exists and return its path.

    What is tested:
        The helper guarantees a secrets subdirectory exists under the provided
        private directory.
    How it's tested:
        The function creates the directory if needed and returns the path.
    Expected result:
        The returned path points to an existing directory at
        <private_dir>/<ENV_DIR_NAME>.
    """
    secrets_dir = private_dir / ENV_DIR_NAME
    secrets_dir.mkdir(parents=True, exist_ok=True)
    return secrets_dir


def write_secrets_file(secrets_dir: Path, filename: str, contents: str) -> None:
    """Ensure a secrets file contains the provided dotenv content.

    What is tested:
        The helper should write a given string to a file inside the secrets
        directory.
    How it's tested:
        It writes the string to <secrets_dir>/<filename> using UTF-8.
    Expected result:
        The file exists with the exact contents passed in.
    """
    file_path = secrets_dir / filename
    file_path.write_text(contents, encoding="utf-8")


def test_load_backend_env_merges_common_and_backend_env_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify backend.env overrides common.env when both define the same key.

    What is tested:
        load_backend_env should merge common.env and backend.env, with backend.env
        taking precedence for duplicate keys.
    How it's tested:
        Create both files with BUCKET_NAME set to different values, point
        PRIVATE_DIR to the temp secrets directory, then call load_backend_env.
    Expected result format:
        The returned BUCKET_NAME value matches the backend.env value.
    """
    # Arrange
    secrets_dir = create_secrets_dir(tmp_path)
    common_contents = f"{REQUIRED_BUCKET_KEY}={COMMON_VALUE}\n"
    write_secrets_file(secrets_dir, COMMON_ENV_FILENAME, common_contents)
    write_secrets_file(
        secrets_dir,
        BACKEND_ENV_FILENAME,
        f"{REQUIRED_BUCKET_KEY}={OVERRIDDEN_VALUE}\n",
    )
    monkeypatch.setenv(PRIVATE_DIR_ENV_VAR, str(tmp_path))

    # Act
    env_values = load_backend_env([REQUIRED_BUCKET_KEY])

    # Assert
    assert env_values[REQUIRED_BUCKET_KEY] == OVERRIDDEN_VALUE


def test_load_backend_env_expands_home_and_env_vars(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify environment placeholders ($HOME, $VAR) are expanded in values.

    What is tested:
        load_backend_env should expand $HOME and other env vars found in
        backend.env values, and resolve optional keys from environment values.
    How it's tested:
        Write backend.env entries containing $HOME and $ENV_ONLY_KEY, set those
        environment variables, then load required and optional keys.
    Expected result format:
        The returned BUCKET_NAME contains the expanded HOME path and the optional
        credentials key equals the referenced environment value.
    """
    # Arrange
    secrets_dir = create_secrets_dir(tmp_path)
    write_secrets_file(
        secrets_dir,
        BACKEND_ENV_FILENAME,
        "\n".join(
            [
                f"{REQUIRED_BUCKET_KEY}=${HOME_ENV_VAR}/{HOME_SUBPATH}",
                f"{OPTIONAL_CREDENTIALS_KEY}=${ENV_VALUE_KEY}",
            ]
        )
        + "\n",
    )
    monkeypatch.setenv(PRIVATE_DIR_ENV_VAR, str(tmp_path))
    monkeypatch.setenv(HOME_ENV_VAR, EXPANDED_HOME_VALUE)
    monkeypatch.setenv(ENV_VALUE_KEY, ENV_VALUE_VALUE)

    # Act
    env_values = load_backend_env(
        [REQUIRED_BUCKET_KEY],
        optional=[OPTIONAL_CREDENTIALS_KEY],
    )

    # Assert
    assert env_values[REQUIRED_BUCKET_KEY] == f"{EXPANDED_HOME_VALUE}/{HOME_SUBPATH}"
    assert env_values[OPTIONAL_CREDENTIALS_KEY] == ENV_VALUE_VALUE


def test_load_backend_env_raises_for_missing_required_key(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify missing required keys raise a RuntimeError mentioning the key.

    What is tested:
        load_backend_env should fail loudly when a required key is missing from
        both files and the process environment.
    How it's tested:
        Create a backend.env without BUCKET_NAME, clear BUCKET_NAME from the
        environment, and call load_backend_env with BUCKET_NAME required.
    Expected result format:
        A RuntimeError is raised and the error message contains BUCKET_NAME.
    """
    # Arrange
    secrets_dir = create_secrets_dir(tmp_path)
    write_secrets_file(secrets_dir, BACKEND_ENV_FILENAME, f"{OTHER_ENV_KEY}=value\n")
    monkeypatch.setenv(PRIVATE_DIR_ENV_VAR, str(tmp_path))
    monkeypatch.delenv(REQUIRED_BUCKET_KEY, raising=False)

    # Act / Assert
    with pytest.raises(RuntimeError, match=REQUIRED_BUCKET_KEY):
        load_backend_env([REQUIRED_BUCKET_KEY])
