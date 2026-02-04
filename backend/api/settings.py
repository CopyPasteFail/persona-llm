"""Settings loader for the backend.

Reads required config from `PRIVATE_DIR`-hosted dotenv files when available or
from process environment when injected (e.g., Cloud Run). Uses pydantic types
to validate shapes and ranges before the app boots.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import overload
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

PERSONA_MAX_CHARS = 50
PERSONA_MAX_WORDS = 4

class Settings(BaseModel):
    """Strongly-typed backend config with range/length guards."""
    PERSONA_NAME: str = Field(..., min_length=1, max_length=PERSONA_MAX_CHARS)
    PROJECT_ID: str = Field(...)
    REGION: str = Field(...)
    LLM_MODEL_NAME: str = Field(default="gemini-2.5-flash")
    INDEX_ENDPOINT_ID: str | None = Field(default=None)
    DEPLOYED_INDEX_ID: str | None = Field(default=None)
    BUCKET_NAME: str | None = Field(default=None)
    DATASET_URI: str | None = Field(default=None)
    DATASET_POINTER_PATH: str | None = Field(default=None)
    CHUNKS_PATH: str | None = Field(default=None)
    VECTOR_BACKEND: str = Field(default="local")
    LLM_BACKEND: str = Field(...)
    API_KEY: str = Field(...)
    JWT_SECRET: str | None = Field(default=None)
    JWT_SESSION_TTL_SECONDS: int = Field(default=3600, ge=300, le=86400)
    SESSION_COOKIE_ENABLED: bool = Field(default=False)
    SESSION_COOKIE_NAME: str = Field(default="session")
    SESSION_COOKIE_SAMESITE: str = Field(default="lax")
    SESSION_COOKIE_SECURE: bool = Field(default=True)
    SESSION_COOKIE_PATH: str = Field(default="/")
    MAX_INPUT_TOKENS: int = Field(default=8000, ge=1, le=10000)
    MAX_OUTPUT_TOKENS: int = Field(..., ge=1, le=4000)
    THINKING_BUDGET_TOKENS: int | None = Field(default=None, ge=0, le=20000)
    INCLUDE_THOUGHTS: bool = Field(default=False)
    REQ_TIMEOUT_MS: int = Field(..., ge=1000, le=60000)
    ENABLE_THINKING_GATING: bool = Field(default=False)
    MOCK_ACCESS_KEYS_PATH: str | None = Field(default=None)
    OPS_AUTH: str = Field(default="enabled")
    OPS_SECRET: str | None = Field(default=None)

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
        if not self.CHUNKS_PATH:
            return ""
        if not self.BUCKET_NAME:
            return ""
        bucket = self.BUCKET_NAME.rstrip("/")
        object_name = self.CHUNKS_PATH.lstrip("/")
        return f"gs://{bucket}/{object_name}"

    @property
    def index_endpoint_path(self) -> str:
        """Normalize index endpoint to a full resource path; tolerate prefilled path."""
        endpoint = (self.INDEX_ENDPOINT_ID or "").strip()
        if not endpoint:
            raise RuntimeError("INDEX_ENDPOINT_ID is required for matching_engine vector backend")
        if "/" in endpoint:
            return endpoint
        return (
            f"projects/{self.PROJECT_ID}/locations/{self.REGION}/indexEndpoints/{endpoint}"
        )

    @property
    def jwt_secret(self) -> str:
        """Return JWT signing secret, preferring JWT_SECRET when set."""
        return self.JWT_SECRET or self.API_KEY

    @property
    def session_ttl_seconds(self) -> int:
        """Session token TTL, capped by access-key expiry in the auth layer."""
        return int(self.JWT_SESSION_TTL_SECONDS or 3600)

    @property
    def session_cookie_enabled(self) -> bool:
        return bool(self.SESSION_COOKIE_ENABLED)

    @property
    def session_cookie_name(self) -> str:
        return self.SESSION_COOKIE_NAME or "session"

    @property
    def session_cookie_samesite(self) -> str:
        value = (self.SESSION_COOKIE_SAMESITE or "lax").lower()
        if value not in {"lax", "strict", "none"}:
            return "lax"
        return value

    @property
    def session_cookie_secure(self) -> bool:
        return bool(self.SESSION_COOKIE_SECURE)

    @property
    def session_cookie_path(self) -> str:
        return self.SESSION_COOKIE_PATH or "/"

    @property
    def request_timeout_seconds(self) -> float:
        """Return the outbound request timeout in seconds."""
        return float(self.REQ_TIMEOUT_MS) / 1000.0

REQUIRED_ENV_VARS = [
    "PERSONA_NAME",
    "PROJECT_ID",
    "REGION",
    "LLM_BACKEND",
    "API_KEY",
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

    missing: list[str] = [str(p) for p in (common_env_path, env_path) if not p.exists()]
    if missing:
        raise RuntimeError(f"Missing secrets file(s): {', '.join(missing)}")

    # Load shared values before backend overrides. OS env still wins.
    load_dotenv(common_env_path, override=False)
    load_dotenv(env_path, override=False)
    return env_path, common_env_path

def _missing_env_vars() -> list[str]:
    """List required env names that are unset/empty, leveraging str typing."""
    required = list(REQUIRED_ENV_VARS)
    if not os.getenv("DATASET_URI"):
        required.append("BUCKET_NAME")
    return [name for name in required if not os.getenv(name)]

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value

@overload
def _env_int(name: str, default: None = None) -> int | None: ...

@overload
def _env_int(name: str, default: int) -> int: ...

def _env_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)

def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default

def load_settings() -> Settings:
    """Load dotenvs when present, verify required envs, and return typed Settings."""
    env_path, common_env_path = _load_env_files_if_available()

    missing_vars = _missing_env_vars()
    if missing_vars:
        hint_parts: list[str] = []
        if env_path and common_env_path:
            hint_parts.append(f"checked {common_env_path}")
            hint_parts.append(f"{env_path}")
        elif os.getenv("PRIVATE_DIR"):
            hint_parts.append(f"PRIVATE_DIR={os.getenv('PRIVATE_DIR')} (secrets not found)")
        else:
            hint_parts.append("set PRIVATE_DIR to your secrets folder or provide env vars directly")
        hint = "; ".join(hint_parts)
        raise RuntimeError(f"Missing required env vars: {sorted(set(missing_vars))}. {hint}")

    dataset_uri = os.getenv("DATASET_URI")
    bucket_name = _require_env("BUCKET_NAME") if not dataset_uri else os.getenv("BUCKET_NAME")
    chunk_path = os.getenv("CHUNKS_PATH")

    vector_backend = (os.getenv("VECTOR_BACKEND") or "local").strip().lower()
    if vector_backend == "matching_engine":
        for required in ("INDEX_ENDPOINT_ID", "DEPLOYED_INDEX_ID"):
            _require_env(required)

    try:
        max_input_tokens = _env_int("MAX_INPUT_TOKENS", 8000)
        max_output_tokens = int(_require_env("MAX_OUTPUT_TOKENS"))
        return Settings(
            PERSONA_NAME=_require_env("PERSONA_NAME"),
            PROJECT_ID=_require_env("PROJECT_ID"),
            REGION=_require_env("REGION"),
            LLM_MODEL_NAME=(os.getenv("LLM_MODEL_NAME") or "gemini-2.5-flash").strip(),
            INDEX_ENDPOINT_ID=os.getenv("INDEX_ENDPOINT_ID"),
            DEPLOYED_INDEX_ID=os.getenv("DEPLOYED_INDEX_ID"),
            BUCKET_NAME=bucket_name,
            DATASET_URI=(dataset_uri or None),
            DATASET_POINTER_PATH=os.getenv("DATASET_POINTER_PATH"),
            CHUNKS_PATH=chunk_path,
            VECTOR_BACKEND=vector_backend,
            LLM_BACKEND=_require_env("LLM_BACKEND"),
            API_KEY=_require_env("API_KEY"),
            JWT_SECRET=os.getenv("JWT_SECRET"),
            JWT_SESSION_TTL_SECONDS=_env_int("JWT_SESSION_TTL_SECONDS", 3600),
            SESSION_COOKIE_ENABLED=_env_bool("SESSION_COOKIE_ENABLED", False),
            SESSION_COOKIE_NAME=os.getenv("SESSION_COOKIE_NAME") or "session",
            SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE") or "lax",
            SESSION_COOKIE_SECURE=_env_bool("SESSION_COOKIE_SECURE", True),
            SESSION_COOKIE_PATH=os.getenv("SESSION_COOKIE_PATH") or "/",
            MAX_INPUT_TOKENS=max_input_tokens or 8000,
            MAX_OUTPUT_TOKENS=max_output_tokens,
            THINKING_BUDGET_TOKENS=_env_int("THINKING_BUDGET_TOKENS"),
            INCLUDE_THOUGHTS=_env_bool("INCLUDE_THOUGHTS", False),
            REQ_TIMEOUT_MS=int(_require_env("REQ_TIMEOUT_MS")),
            ENABLE_THINKING_GATING=_env_bool("ENABLE_THINKING_GATING", False),
            MOCK_ACCESS_KEYS_PATH=os.getenv("MOCK_ACCESS_KEYS_PATH"),
            OPS_AUTH=os.getenv("OPS_AUTH") or "enabled",
            OPS_SECRET=os.getenv("OPS_SECRET"),
        )
    except ValidationError as e:
        fields = [err["loc"][0] for err in e.errors()]
        raise RuntimeError(f"Invalid settings. Fix env vars: {sorted(set(fields))}")

settings: Settings = load_settings()
__all__ = ["Settings", "settings"]
