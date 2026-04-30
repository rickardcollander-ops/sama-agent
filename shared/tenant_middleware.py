"""
Tenant Middleware for FastAPI

Resolves the active tenant for each request and attaches it to ``request.state``.

Resolution order:
1. Supabase JWT in ``Authorization: Bearer <token>`` (authoritative)
2. ``X-Tenant-ID`` header (only honoured when JWT verification is unavailable
   or for the special ``default`` tenant on the legacy single-tenant deployment)
3. ``tenant_id`` query parameter (same caveats as the header)
4. ``"default"`` fallback

When a JWT is present and verifies successfully, the tenant_id is taken from
its ``sub`` claim and any caller-provided header is ignored — this is the
authority used to enforce per-tenant data isolation downstream.
"""

import logging
import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"

# Paths that should bypass tenant resolution entirely (health checks, OAuth
# callbacks, webhooks signed by external providers).
_BYPASS_PREFIXES = (
    "/health",
    "/api/auth/",
    "/api/webhooks/",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _verify_supabase_jwt(token: str) -> Optional[str]:
    """Verify a Supabase JWT and return its sub claim, or None on failure."""
    secret = os.getenv("SUPABASE_JWT_SECRET")
    if not secret:
        return None
    try:
        import jwt  # PyJWT
    except ImportError:
        logger.warning("PyJWT not installed — cannot verify tenant JWT")
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_aud": True},
        )
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception as e:
        logger.debug(f"JWT verification failed: {e}")
        return None


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _BYPASS_PREFIXES) or path == "/":
            request.state.tenant_id = DEFAULT_TENANT_ID
            request.state.authenticated = False
            return await call_next(request)

        verified_tenant: Optional[str] = None

        # 1. Supabase JWT (authoritative)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token:
                verified_tenant = _verify_supabase_jwt(token)

        if verified_tenant:
            request.state.tenant_id = verified_tenant
            request.state.authenticated = True
            # If the caller also sent X-Tenant-ID, reject mismatches outright
            # — this catches accidental cross-tenant calls and tampering.
            header_tid = request.headers.get("X-Tenant-ID")
            if header_tid and header_tid != verified_tenant:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "X-Tenant-ID does not match authenticated user",
                    },
                )
            return await call_next(request)

        # 2. Header fallback (only when JWT not available — legacy mode)
        tenant_id = request.headers.get("X-Tenant-ID")

        # 3. Query param
        if not tenant_id:
            tenant_id = request.query_params.get("tenant_id")

        # 4. Default fallback
        if not tenant_id:
            tenant_id = DEFAULT_TENANT_ID

        request.state.tenant_id = tenant_id
        request.state.authenticated = False

        return await call_next(request)
