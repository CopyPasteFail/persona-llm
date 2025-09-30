from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

PERSONA_MAX_CHARS = 50
PERSONA_MAX_WORDS = 4

class Settings(BaseModel):
    PERSONA_NAME: str = Field(..., min_length=1, max_length=PERSONA_MAX_CHARS)
    PROJECT_ID: str = Field(...)
    REGION: str = Field(...)
    INDEX_ENDPOINT_ID: str = Field(...)
    DEPLOYED_INDEX_ID: str = Field(...)
    CHUNKS_URI: str = Field(...)
    API_KEY: str = Field(...)
    MAX_INPUT_TOKENS: int = Field(..., ge=1, le=10000)
    MAX_OUTPUT_TOKENS: int = Field(..., ge=1, le=2000)
    REQ_TIMEOUT_MS: int = Field(..., ge=1000, le=60000)

    @field_validator("PERSONA_NAME")
    @classmethod
    def persona_max_four_words(cls, v: str) -> str:
        name = v.strip()
        if len(name.split()) > PERSONA_MAX_WORDS:
            raise ValueError(f"PERSONA_NAME must not exceed {PERSONA_MAX_WORDS} words")
        return name

def load_settings() -> Settings:
    env_dir = os.getenv("PRIVATE_DIR")
    if not env_dir:
        raise RuntimeError("PRIVATE_DIR is not set. It must point to the private folder.")
    env_path = Path(env_dir).expanduser().resolve() / "secrets" / "backend.env"
    common_env_path = env_path.with_name("common.env")

    if not common_env_path.exists():
        raise RuntimeError(f"Missing secrets file: {common_env_path}")
    if not env_path.exists():
        raise RuntimeError(f"Missing secrets file: {env_path}")

    # Load shared values before backend overrides. OS env still wins.
    load_dotenv(common_env_path, override=False)
    load_dotenv(env_path, override=False)

    chunk_path = os.getenv("CHUNKS_PATH")
    bucket_name = os.getenv("BUCKET_NAME")
    chunk_uri = f"gs://{bucket_name.rstrip('/')}/{chunk_path.lstrip('/')}"


    try:
        return Settings(
            PERSONA_NAME=os.getenv("PERSONA_NAME"),
            PROJECT_ID=os.getenv("PROJECT_ID"),
            REGION=os.getenv("REGION"),
            INDEX_ENDPOINT_ID=os.getenv("INDEX_ENDPOINT_ID"),
            DEPLOYED_INDEX_ID=os.getenv("DEPLOYED_INDEX_ID"),
            CHUNKS_URI=chunk_uri,
            API_KEY=os.getenv("API_KEY"),
            MAX_INPUT_TOKENS=os.getenv("MAX_INPUT_TOKENS"),
            MAX_OUTPUT_TOKENS=os.getenv("MAX_OUTPUT_TOKENS"),
            REQ_TIMEOUT_MS=os.getenv("REQ_TIMEOUT_MS"),
        )
    except ValidationError as e:
        fields = [err["loc"][0] for err in e.errors()]
        raise RuntimeError(f"Invalid settings. Fix env vars: {sorted(set(fields))}")

settings = load_settings()
