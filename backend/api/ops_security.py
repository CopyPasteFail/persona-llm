from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from .settings import settings

# HTTP header name, not a secret value.
OPS_SECRET_HEADER = "x-ops-secret"  # noqa: S105  # nosec B105
OPS_AUTH_DISABLED = "disabled"
OPS_AUTH_ERROR = "ops_auth_required"
OPS_AUTH_MISCONFIGURED = "ops_secret_not_configured"


def require_ops_secret(
    ops_secret: Optional[str] = Header(None, alias=OPS_SECRET_HEADER),
) -> None:
    """Validate the ops secret header unless OPS_AUTH is disabled."""
    if (settings.OPS_AUTH or "").strip().lower() == OPS_AUTH_DISABLED:
        return
    expected = (settings.OPS_SECRET or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail=OPS_AUTH_MISCONFIGURED)
    if not ops_secret or ops_secret != expected:
        raise HTTPException(status_code=403, detail=OPS_AUTH_ERROR)
