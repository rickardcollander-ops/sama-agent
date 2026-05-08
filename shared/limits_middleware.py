"""
Body-size and request-timeout middleware.

Body cap protects against memory-exhaustion via huge JSON payloads. Timeout
protects worker threads from slow upstream calls leaking out of route
handlers (an agent that hangs on Anthropic should still let the HTTP request
return cleanly).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_BODY_BYTES = _int_env("MAX_BODY_BYTES", 2_000_000)
REQUEST_TIMEOUT_S = _int_env("REQUEST_TIMEOUT_S", 120)


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (>{MAX_BODY_BYTES} bytes)"},
            )
        return await call_next(request)


class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        try:
            return await asyncio.wait_for(call_next(request), timeout=REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("request_timeout path=%s", request.url.path)
            return JSONResponse(
                status_code=504,
                content={"detail": f"Request exceeded {REQUEST_TIMEOUT_S}s timeout"},
            )
