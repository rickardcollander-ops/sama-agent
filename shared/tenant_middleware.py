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
4. ``"default"`` sentinel — set on bypass paths only (health, auth, webhooks).
   Data-bearing routes never reach this fallback; see enforcement below.

Multi-user account support:
When a JWT-authenticated user (sub=B) sends X-Sama-Account-Id=A (a shared
account owned by user A), the middleware verifies that B is an active member
of A's account via the account_members table before allowing the request.
This lets invited team members access shared account data without the backend
blind-trusting the header.

Protected API prefixes (data-bearing routes) reject any request that doesn't
resolve to a real ``account_id`` — either a verified Supabase JWT, an
explicit ``X-Sama-Account-Id`` header, or an allowlisted ``X-Tenant-ID``.
The fallback to the legacy ``"default"`` tenant is gone by default because
that partition holds the original Successifier rows from before multi-
tenancy and was leaking to any caller who arrived without identification.

To restore the historical permissive behaviour (e.g. for ad-hoc scripts
during a migration window), set ``ALLOW_ANONYMOUS_TENANT_FALLBACK=1``. The
older ``REQUIRE_TENANT_HEADERS`` flag is still honoured but is now
redundant — protection is on by default.

Read-side concession: anonymous GET/HEAD on protected prefixes returns
200 ``{}`` instead of 401, so dashboard polling during pre-auth render
doesn't fill the browser console with red. No data leaks — the empty
payload is fixed in middleware and never reaches a route. Writes
(POST/PUT/PATCH/DELETE) still return 401. Set
``STRICT_PROTECTED_GET_AUTH=1`` to restore strict 401 on reads too.

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
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"

# Per-tenant cooldown for the noisy "legacy_header_rejected" warning.
_LEGACY_REJECT_LOG_INTERVAL_S = 300.0
_legacy_reject_last_log: dict[str, float] = {}


def _should_log_legacy_reject(tenant: str) -> bool:
    now = time.monotonic()
    last = _legacy_reject_last_log.get(tenant, 0.0)
    if now - last < _LEGACY_REJECT_LOG_INTERVAL_S:
        return False
    _legacy_reject_last_log[tenant] = now
    return True

# Paths that should bypass tenant resolution entirely.
_BYPASS_PREFIXES = (
    "/health",
    "/api/auth/",
    "/api/webhooks/",
    "/api/subscriptions/webhook",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _legacy_allowlist() -> set[str]:
    """Tenants still allowed to use the unverified X-Tenant-ID legacy header."""
    raw = os.getenv("LEGACY_TENANT_HEADERS_ALLOW", "")
    return {t.strip() for t in raw.split(",") if t.strip()}

_PROTECTED_PREFIXES = (
    "/api/seo/",
    "/api/ai-visibility/",
    "/api/content/",
    "/api/strategy/",
    "/api/dashboard/",
    "/api/analytics/",
    "/api/ads/",
    "/api/social/",
    "/api/reviews/",
    "/api/agents/",
    "/api/alerts/",
    "/api/leads/",
    "/api/notifications/",
    "/api/automation/",
    "/api/improvements/",
    "/api/orchestrator/",
    "/api/gtm/",
    "/api/goals/",
)


def _truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


_site_owner_cache: dict[str, tuple[str, float]] = {}
_SITE_CACHE_TTL_S = 60.0


def _site_belongs_to_account(site_id: str, account_id: str) -> bool:
    """Cheap, cached check that ``site_id`` belongs to ``account_id``."""
    cached = _site_owner_cache.get(site_id)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0] == account_id
    try:
        from shared.database import get_supabase  # local import — avoids cycle
        sb = get_supabase()
        if not sb:
            return True  # fail-open if DB not configured (e.g. tests)
        res = (
            sb.table("user_sites")
            .select("user_id")
            .eq("id", site_id)
            .limit(1)
            .execute()
        )
        owner = (res.data[0]["user_id"] if res.data else "")
        _site_owner_cache[site_id] = (owner, now + _SITE_CACHE_TTL_S)
        return owner == account_id
    except Exception as e:
        logger.warning("site_owner_lookup_failed site=%s err=%s", site_id, e)
        return True  # fail-open — log and let through; alerts surface this


# Cache: (account_id, user_id) -> (is_member: bool, expires_at: float)
_member_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_MEMBER_CACHE_TTL_S = 60.0


def _is_account_member(account_id: str, user_id: str) -> bool:
    """Cached check that ``user_id`` is an active member of ``account_id``.

    Used to allow invited team members (JWT sub != account_id) to act on
    shared accounts without blind-trusting the X-Sama-Account-Id header.
    Cache TTL is 60 s so revoked memberships propagate within a minute.
    """
    key = (account_id, user_id)
    cached = _member_cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    try:
        from shared.database import get_supabase  # local import — avoids cycle
        sb = get_supabase()
        if not sb:
            return False  # fail-closed: no DB = no member access
        res = (
            sb.table("account_members")
            .select("id")
            .eq("account_id", account_id)
            .eq("user_id", user_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        is_member = len(res.data) > 0
        _member_cache[key] = (is_member, now + _MEMBER_CACHE_TTL_S)
        return is_member
    except Exception as e:
        logger.warning(
            "account_member_lookup_failed account=%s user=%s err=%s",
            account_id, user_id, e,
        )
        return False  # fail-closed on error


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
            request.state.tenant_resolved = False
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

        is_protected = any(path.startswith(p) for p in _PROTECTED_PREFIXES)
        allow_anon_fallback = _truthy(os.getenv("ALLOW_ANONYMOUS_TENANT_FALLBACK"))
        require_auth = _truthy(os.getenv("REQUIRE_AUTHENTICATED_TENANT"))

        if verified_account:
            if header_account_id and header_account_id != verified_account:
                # The JWT user is requesting access to a different account.
                # Allow if they are an active member of that account — this is
                # the normal case for invited team members (JWT sub=B, account=A).
                if not _is_account_member(header_account_id, verified_account):
                    logger.warning(
                        "tenant_mismatch account=%s header=%s path=%s",
                        verified_account, header_account_id, path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "X-Sama-Account-Id does not match authenticated user and user is not a member of that account"},
                    )
                account_id = header_account_id
            else:
                # Same check for the legacy header so old clients can't slip past.
                if legacy_tid and not header_account_id and legacy_tid != verified_account:
                    logger.warning(
                        "legacy_tenant_mismatch account=%s legacy=%s path=%s",
                        verified_account, legacy_tid, path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "X-Tenant-ID does not match authenticated user"},
                    )
                account_id = verified_account

            # If client sent X-Sama-Site-Id, verify the site belongs to the
            # authenticated account before honouring it.
            if header_site_id and _truthy(os.getenv("STRICT_SITE_VALIDATION")):
                if not _site_belongs_to_account(header_site_id, account_id):
                    logger.warning(
                        "site_mismatch account=%s site=%s path=%s",
                        account_id, header_site_id, path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "X-Sama-Site-Id does not belong to authenticated account"},
                    )
            authenticated = True
        else:
            # No JWT. Decide what to do with header-only callers.
            if is_protected and require_auth:
                logger.info("auth_required path=%s", path)
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required for protected route"},
                )
            if legacy_tid and legacy_tid not in _legacy_allowlist():
                if _should_log_legacy_reject(legacy_tid):
                    logger.warning(
                        "legacy_header_rejected tenant=%s path=%s "
                        "(add to LEGACY_TENANT_HEADERS_ALLOW or migrate to JWT)",
                        legacy_tid, path,
                    )
                legacy_tid = None
            elif legacy_tid:
                logger.info("legacy_header_used tenant=%s path=%s", legacy_tid, path)
            account_id = header_account_id or legacy_tid
            authenticated = False

        site_id = header_site_id or legacy_tid

        if is_protected and not account_id and not allow_anon_fallback:
            method = request.method.upper()
            strict_get = _truthy(os.getenv("STRICT_PROTECTED_GET_AUTH"))
            if method in ("GET", "HEAD") and not strict_get:
                logger.info(
                    "tenant_unauth_get_empty path=%s method=%s",
                    path, method,
                )
                return JSONResponse(status_code=200, content={})
            logger.warning(
                "tenant_required path=%s method=%s authenticated=%s",
                path, method, authenticated,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        "Missing tenant context — send a Supabase Bearer "
                        "token or X-Sama-Account-Id."
                    ),
                },
            )

        tenant_id = site_id or account_id or DEFAULT_TENANT_ID
        tenant_resolved = bool(site_id or account_id)

        request.state.tenant_id = tenant_id
        request.state.account_id = account_id
        request.state.site_id = site_id
        request.state.site_domain = header_site_domain
        request.state.authenticated = authenticated
        request.state.tenant_resolved = tenant_resolved

        return await call_next(request)
