from __future__ import annotations

import os
import re
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

# Simple, explicit placeholder checks so production never boots with junk values.
PLACEHOLDERS = {
    "changeme",
    "your-project-id",
    "your-index-id",
    "projects/.../indexEndpoints/...",  # typical pattern
    "your-deployed-id",
    "gs://bucket/chunks-<sha>.jsonl.gz",
    "your-api-key",
}


class Settings(BaseModel):
    PROJECT_ID: str = Field(...)
    REGION: str = Field(...)
    INDEX_ENDPOINT_ID: str = Field(...)
    DEPLOYED_INDEX_ID: str = Field(...)
    CHUNKS_URI: str = Field(...)
    API_KEY: str = Field(...)

    MAX_INPUT_TOKENS: int = Field(3000, ge=1, le=10000)
    MAX_OUTPUT_TOKENS: int = Field(180, ge=1, le=2000)
    REQ_TIMEOUT_MS: int = Field(20000, ge=1000, le=60000)

    @field_validator(
        "PROJECT_ID",
        "REGION",
        "INDEX_ENDPOINT_ID",
        "DEPLOYED_INDEX_ID",
        "CHUNKS_URI",
        "API_KEY",
    )
    @classmethod
    def no_placeholders(cls, v: str) -> str:
        if not v or v.strip() in PLACEHOLDERS:
            raise ValueError("placeholder value")
        # basic sanity for GCS and resource paths without being too strict
        if "gs://" in v and not re.match(r"^gs://[^/]+/.+", v):
            raise ValueError("invalid GCS URI")
        if "indexEndpoints" in v and "projects/" not in v:
            raise ValueError("invalid index endpoint resource path")
        return v


def load_settings() -> Settings:
    load_dotenv()
    try:
        return Settings(
            PROJECT_ID=os.getenv("PROJECT_ID"),
            REGION=os.getenv("REGION"),
            INDEX_ENDPOINT_ID=os.getenv("INDEX_ENDPOINT_ID"),
            DEPLOYED_INDEX_ID=os.getenv("DEPLOYED_INDEX_ID"),
            CHUNKS_URI=os.getenv("CHUNKS_URI"),
            API_KEY=os.getenv("API_KEY"),
            MAX_INPUT_TOKENS=int(os.getenv("MAX_INPUT_TOKENS") or 3000),
            MAX_OUTPUT_TOKENS=int(os.getenv("MAX_OUTPUT_TOKENS") or 180),
            REQ_TIMEOUT_MS=int(os.getenv("REQ_TIMEOUT_MS") or 20000),
        )
    except ValidationError as e:
        fields = [err["loc"][0] for err in e.errors()]
        raise RuntimeError(f"Invalid settings. Fix env vars: {sorted(set(fields))}")


settings = load_settings()
