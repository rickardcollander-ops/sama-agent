"""
Tenant Middleware for FastAPI

Resolves the active tenant for each request and attaches it to ``request.state``.

The dashboard now sends a richer tenant context as three headers:
- ``X-Sama-Account-Id``  — owning organisation/workspace (uuid)
- ``X-Sama-Site-Id``     — specific site within that account (uuid)
- ``X-Sama-Site-Domain`` — domain string of the active site (informational)

Resolution order for ``account_id`` / ``site_id``:
1. Explicit ``X-Sama-Account-Id`` / ``X-Sama-Site-Id`` headers (preferred)
2. Legacy ``X-Tenant-ID`` header (mapped to *both* account_id and site_id —
   this is the bridge for callers that haven't migrated yet)
3. Supabase JWT ``sub`` claim (legacy single-tenant deployments)
4. ``"default"`` fallback (only honoured when ``REQUIRE_TENANT_HEADERS`` is
   not enabled and the path is unprotected)

When ``REQUIRE_TENANT_HEADERS`` is set to a truthy value, requests to the
protected API prefixes below must carry an ``X-Sama-Account-Id`` (or fall back
to a verified JWT, or the legacy ``X-Tenant-ID``) — anything else gets a
400. This locks out the anonymous-call class that historically returned
Successifier data to callers who never identified themselves.

``request.state`` is populated with:
- ``request.state.tenant_id``   — best-effort tenant identifier (site_id when
  available, otherwise account_id, otherwise ``"default"``). Existing routes
  that read ``tenant_id`` continue to work unchanged.
- ``request.state.account_id``  — the owning account (or None)
- ``request.state.site_id``     — the active site (or None)
- ``request.state.site_domain`` — the site domain string (or None)
- ``request.state.authenticated`` — True iff a JWT verified successfully.
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

# Routes where tenant context is required when REQUIRE_TENANT_HEADERS is on.
# The historical anonymous-call leak (any unauth caller saw the default
# Successifier tenant's data) all happened through these prefixes.
_PROTECTED_PREFIXES = (
    "/api/seo/",
    "/api/ai-visibility/",
    "/api/content/",
    "/api/strategy/",
)


def _truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


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
            request.state.account_id = None
            request.state.site_id = None
            request.state.site_domain = None
            request.state.authenticated = False
            return await call_next(request)

        # New explicit tenant headers from the dashboard
        header_account_id = request.headers.get("X-Sama-Account-Id") or None
        header_site_id = request.headers.get("X-Sama-Site-Id") or None
        header_site_domain = request.headers.get("X-Sama-Site-Domain") or None

        # Legacy single-tenant bridge: X-Tenant-ID (or ?tenant_id=) maps to both
        # account_id and site_id when the new headers aren't supplied.
        legacy_tid = (
            request.headers.get("X-Tenant-ID")
            or request.query_params.get("tenant_id")
        )

        # JWT verification — still authoritative for the account dimension when
        # present, but the dashboard's site_id header is what selects which
        # site within that account is being viewed.
        verified_account: Optional[str] = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token:
                verified_account = _verify_supabase_jwt(token)

        if verified_account:
            # If the caller also sent X-Sama-Account-Id, reject mismatches —
            # this catches accidental cross-tenant calls and tampering.
            if header_account_id and header_account_id != verified_account:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "X-Sama-Account-Id does not match authenticated user",
                    },
                )
            # Same check for the legacy header so old clients can't slip past.
            if legacy_tid and not header_account_id and legacy_tid != verified_account:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "X-Tenant-ID does not match authenticated user",
                    },
                )
            account_id = verified_account
            authenticated = True
        else:
            account_id = header_account_id or legacy_tid
            authenticated = False

        site_id = header_site_id or legacy_tid

        # Enforcement: protected routes must carry a tenant context when
        # REQUIRE_TENANT_HEADERS is on. We only enforce on protected prefixes
        # so unauthed health/usage calls keep working.
        require = _truthy(os.getenv("REQUIRE_TENANT_HEADERS"))
        is_protected = any(path.startswith(p) for p in _PROTECTED_PREFIXES)
        if require and is_protected and not account_id:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Missing tenant context — send X-Sama-Account-Id "
                        "(or X-Tenant-ID for legacy callers, or a Supabase "
                        "Bearer token)."
                    ),
                },
            )

        # tenant_id is the field every existing route reads. Prefer site_id
        # (the granular dimension), fall back to account_id, then 'default'.
        tenant_id = site_id or account_id or DEFAULT_TENANT_ID

        request.state.tenant_id = tenant_id
        request.state.account_id = account_id
        request.state.site_id = site_id
        request.state.site_domain = header_site_domain
        request.state.authenticated = authenticated

        return await call_next(request)
