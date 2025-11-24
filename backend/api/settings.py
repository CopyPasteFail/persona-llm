"""Settings loader for the backend.

Reads required config from `PRIVATE_DIR`-hosted dotenv files when available or
from process environment when injected (e.g., Cloud Run). Uses pydantic types
to validate shapes and ranges before the app boots.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

PERSONA_MAX_CHARS = 50
PERSONA_MAX_WORDS = 4

class Settings(BaseModel):
    """Strongly-typed backend config with range/length guards."""
    PERSONA_NAME: str = Field(..., min_length=1, max_length=PERSONA_MAX_CHARS)
    PROJECT_ID: str = Field(...)
    REGION: str = Field(...)
    INDEX_ENDPOINT_ID: str = Field(...)
    DEPLOYED_INDEX_ID: str = Field(...)
    BUCKET_NAME: str = Field(...)
    CHUNKS_PATH: str = Field(...)
    API_KEY: str = Field(...)
    MAX_INPUT_TOKENS: int = Field(..., ge=1, le=10000)
    MAX_OUTPUT_TOKENS: int = Field(..., ge=1, le=2000)
    REQ_TIMEOUT_MS: int = Field(..., ge=1000, le=60000)

    @field_validator("PERSONA_NAME")
    @classmethod
    def persona_max_four_words(cls, v: str) -> str:
        """Ensure persona name stays short; rely on string typing for trim/len."""
        name = v.strip()
        if len(name.split()) > PERSONA_MAX_WORDS:
            raise ValueError(f"PERSONA_NAME must not exceed {PERSONA_MAX_WORDS} words")
        return name

    @property
    def chunks_uri(self) -> str:
        """Return a gs:// URI built from typed bucket/path fields."""
        bucket = self.BUCKET_NAME.rstrip("/")
        object_name = self.CHUNKS_PATH.lstrip("/")
        return f"gs://{bucket}/{object_name}"

    @property
    def index_endpoint_path(self) -> str:
        """Normalize index endpoint to a full resource path; tolerate prefilled path."""
        endpoint = (self.INDEX_ENDPOINT_ID or "").strip()
        if "/" in endpoint:
            return endpoint
        return (
            f"projects/{self.PROJECT_ID}/locations/{self.REGION}/indexEndpoints/{endpoint}"
        )

REQUIRED_ENV_VARS = [
    "PERSONA_NAME",
    "PROJECT_ID",
    "REGION",
    "INDEX_ENDPOINT_ID",
    "DEPLOYED_INDEX_ID",
    "BUCKET_NAME",
    "CHUNKS_PATH",
    "API_KEY",
    "MAX_INPUT_TOKENS",
    "MAX_OUTPUT_TOKENS",
    "REQ_TIMEOUT_MS",
]

def _load_env_files_if_available() -> tuple[Path | None, Path | None]:
    """Load dotenv files from PRIVATE_DIR when set; otherwise skip.

    Returns the resolved backend and common env paths (or Nones). Tuple typing
    mirrors the two expected files.
    """
    env_dir = os.getenv("PRIVATE_DIR")
    if not env_dir:
        return None, None

    env_path = Path(env_dir).expanduser().resolve() / "secrets" / "backend.env"
    common_env_path = env_path.with_name("common.env")

    missing = [str(p) for p in (common_env_path, env_path) if not p.exists()]
    if missing:
        raise RuntimeError(f"Missing secrets file(s): {', '.join(missing)}")

    # Load shared values before backend overrides. OS env still wins.
    load_dotenv(common_env_path, override=False)
    load_dotenv(env_path, override=False)
    return env_path, common_env_path

def _missing_env_vars() -> list[str]:
    """List required env names that are unset/empty, leveraging str typing."""
    return [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]

def load_settings() -> Settings:
    """Load dotenvs when present, verify required envs, and return typed Settings."""
    env_path, common_env_path = _load_env_files_if_available()

    missing_vars = _missing_env_vars()
    if missing_vars:
        hint_parts = []
        if env_path and common_env_path:
            hint_parts.append(f"checked {common_env_path}")
            hint_parts.append(f"{env_path}")
        elif os.getenv("PRIVATE_DIR"):
            hint_parts.append(f"PRIVATE_DIR={os.getenv('PRIVATE_DIR')} (secrets not found)")
        else:
            hint_parts.append("set PRIVATE_DIR to your secrets folder or provide env vars directly")
        hint = "; ".join(hint_parts)
        raise RuntimeError(f"Missing required env vars: {sorted(set(missing_vars))}. {hint}")

    bucket_name = os.getenv("BUCKET_NAME")
    chunk_path = os.getenv("CHUNKS_PATH")

    try:
        return Settings(
            PERSONA_NAME=os.getenv("PERSONA_NAME"),
            PROJECT_ID=os.getenv("PROJECT_ID"),
            REGION=os.getenv("REGION"),
            INDEX_ENDPOINT_ID=os.getenv("INDEX_ENDPOINT_ID"),
            DEPLOYED_INDEX_ID=os.getenv("DEPLOYED_INDEX_ID"),
            BUCKET_NAME=bucket_name,
            CHUNKS_PATH=chunk_path,
            API_KEY=os.getenv("API_KEY"),
            MAX_INPUT_TOKENS=os.getenv("MAX_INPUT_TOKENS"),
            MAX_OUTPUT_TOKENS=os.getenv("MAX_OUTPUT_TOKENS"),
            REQ_TIMEOUT_MS=os.getenv("REQ_TIMEOUT_MS"),
        )
    except ValidationError as e:
        fields = [err["loc"][0] for err in e.errors()]
        raise RuntimeError(f"Invalid settings. Fix env vars: {sorted(set(fields))}")

settings = load_settings()
