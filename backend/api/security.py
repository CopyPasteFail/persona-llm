from fastapi import Header, HTTPException, Request
from typing import Optional
from collections import deque
import time
from .settings import settings

PER_MINUTE = 10
PER_DAY = 100
WINDOW_MIN = 60.0
WINDOW_DAY = 86400.0

_hits_min = {}
_hits_day = {}

async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not x_api_key or x_api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="forbidden")

async def check_rate_limit_dependency(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    dq1 = _hits_min.setdefault(ip, deque())
    dq2 = _hits_day.setdefault(ip, deque())
    while dq1 and now - dq1[0] > WINDOW_MIN:
        dq1.popleft()
    while dq2 and now - dq2[0] > WINDOW_DAY:
        dq2.popleft()
    if len(dq1) >= PER_MINUTE or len(dq2) >= PER_DAY:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    dq1.append(now)
    dq2.append(now)
