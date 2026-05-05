"""
Google OAuth Routes
OAuth2 flows for connecting Google Search Console, Analytics GA4, and Ads.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Fallback when no return_url is supplied or it fails the allowlist.
DEFAULT_RETURN_URL = "https://sama.successifier.com/c/settings"

# Hosts allowed as OAuth return targets. Add/remove via env if needed.
ALLOWED_RETURN_HOSTS = {
    "sama.successifier.com",
    "sama-dashboard-alpha.vercel.app",
    "localhost",
    "127.0.0.1",
}

SERVICE_SCOPES = {
    "search_console": "https://www.googleapis.com/auth/webmasters.readonly",
    "analytics": "https://www.googleapis.com/auth/analytics.readonly",
    "ads": "https://www.googleapis.com/auth/adwords",
}

# Always include openid + email so we can show the user which Google
# account is actually connected (otherwise users who linked the wrong
# account have no way to tell, and can't pick a GA4 property because
# their account has no GA4 access).
IDENTITY_SCOPES = "openid email"

VALID_SERVICES = set(SERVICE_SCOPES.keys())

GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _redirect_uri() -> str:
    return settings.GOOGLE_OAUTH_REDIRECT_URI


def _safe_return_url(candidate: Optional[str]) -> str:
    """Validate return_url against the allowlist; fall back on the default."""
    if not candidate:
        return DEFAULT_RETURN_URL
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return DEFAULT_RETURN_URL
    if parsed.scheme not in {"http", "https"}:
        return DEFAULT_RETURN_URL
    if parsed.hostname not in ALLOWED_RETURN_HOSTS:
        logger.warning(f"Rejected return_url with disallowed host: {parsed.hostname}")
        return DEFAULT_RETURN_URL
    return candidate


def _append_query(url: str, params: dict) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(params)}"


async def _fetch_google_account_email(access_token: str) -> Optional[str]:
    """Best-effort fetch of the connected Google account's email.

    Returns None on any error — we never want a userinfo glitch to break the
    OAuth callback. The email is informational (shown in the UI), so a missing
    value just degrades to "Email unknown".
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            logger.warning(f"Google userinfo {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        email = data.get("email")
        return email if isinstance(email, str) else None
    except Exception as exc:
        logger.warning(f"Google userinfo fetch failed: {exc}")
        return None


# ── Connect: redirect to Google consent screen ─────────────────────────────

@router.get("/connect")
async def google_connect(
    service: str = Query(..., description="One of: search_console, analytics, ads"),
    tenant_id: str = Query("default"),
    return_url: Optional[str] = Query(None, description="Where to redirect after OAuth completes"),
):
    """Generate Google OAuth URL and redirect user to consent screen."""
    if service not in VALID_SERVICES:
        raise HTTPException(status_code=400, detail=f"Invalid service '{service}'. Must be one of {sorted(VALID_SERVICES)}")

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth credentials not configured on server")

    safe_return = _safe_return_url(return_url)

    state = json.dumps({
        "tenant_id": tenant_id,
        "service": service,
        "return_url": safe_return,
    })

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": f"{SERVICE_SCOPES[service]} {IDENTITY_SCOPES}",
        "access_type": "offline",
        # `select_account` forces Google to show the account chooser even when
        # the user is signed in to a single account, so they can deliberately
        # pick (or switch) which Google account to authorize. `consent` keeps
        # the consent screen so we always get a refresh_token back.
        "prompt": "select_account consent",
        "include_granted_scopes": "true",
        "state": state,
    }

    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=url)


# ── Callback: exchange code for tokens ──────────────────────────────────────

@router.get("/callback")
async def google_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle OAuth callback from Google, store tokens, redirect to caller."""

    # Decode state up front so we know where to redirect on error.
    return_url = DEFAULT_RETURN_URL
    tenant_id: Optional[str] = None
    service: Optional[str] = None
    state_ok = False

    if state:
        try:
            state_data = json.loads(state)
            tenant_id = state_data.get("tenant_id")
            service = state_data.get("service")
            return_url = _safe_return_url(state_data.get("return_url"))
            state_ok = bool(tenant_id and service)
        except (json.JSONDecodeError, TypeError):
            state_ok = False

    if error:
        logger.warning(f"Google OAuth error: {error}")
        return RedirectResponse(url=_append_query(return_url, {"google_error": error}))

    if not code or not state_ok:
        return RedirectResponse(url=_append_query(return_url, {"google_error": "missing_params"}))

    if service not in VALID_SERVICES:
        return RedirectResponse(url=_append_query(return_url, {"google_error": "invalid_service"}))

    # Exchange auth code for tokens
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            })

        if resp.status_code != 200:
            logger.error(f"Google token exchange failed: {resp.status_code} {resp.text}")
            return RedirectResponse(url=_append_query(return_url, {"google_error": "token_exchange_failed"}))

        token_data = resp.json()
    except Exception as exc:
        logger.error(f"Google token exchange exception: {exc}")
        return RedirectResponse(url=_append_query(return_url, {"google_error": "token_exchange_error"}))

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token:
        return RedirectResponse(url=_append_query(return_url, {"google_error": "no_access_token"}))

    token_expiry = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Resolve the Google account email so the UI can show which account is
    # connected and offer a "switch account" flow if it's the wrong one.
    account_email = await _fetch_google_account_email(access_token)

    # Store in Supabase
    try:
        sb = get_supabase()
        row = {
            "tenant_id": tenant_id,
            "service": service,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_expiry": token_expiry,
            "scopes": SERVICE_SCOPES[service],
            "connected_at": now_iso,
            "created_at": now_iso,
        }
        if account_email:
            row["account_email"] = account_email
        try:
            sb.table("google_connections").upsert(row, on_conflict="tenant_id,service").execute()
        except Exception as exc:
            # Older deployments may not have run migration 031 yet (no
            # `account_email` column). Retry without it instead of failing
            # the whole connect flow.
            if account_email and "account_email" in str(exc):
                row.pop("account_email", None)
                sb.table("google_connections").upsert(row, on_conflict="tenant_id,service").execute()
            else:
                raise
        logger.info(f"Google {service} connected for tenant {tenant_id} ({account_email or 'email unknown'})")
    except Exception as exc:
        logger.error(f"Failed to store Google tokens: {exc}")
        return RedirectResponse(url=_append_query(return_url, {"google_error": "storage_failed"}))

    return RedirectResponse(url=_append_query(return_url, {"google_connected": service}))


# ── Disconnect ──────────────────────────────────────────────────────────────

@router.delete("/disconnect")
async def google_disconnect(
    service: str = Query(..., description="One of: search_console, analytics, ads"),
    tenant_id: str = Query("default"),
):
    """Remove stored tokens for a Google service."""
    if service not in VALID_SERVICES:
        raise HTTPException(status_code=400, detail=f"Invalid service '{service}'")

    try:
        sb = get_supabase()
        sb.table("google_connections").delete().eq(
            "tenant_id", tenant_id
        ).eq("service", service).execute()
        return {"success": True, "service": service}
    except Exception as exc:
        logger.error(f"google_disconnect error: {exc}")
        return {"success": False, "error": str(exc)}


# ── Connection status ───────────────────────────────────────────────────────

@router.get("/status")
async def google_status(tenant_id: str = Query("default")):
    """Return connection status for each Google service.

    Includes the connected Google account's email when available so the
    dashboard can show "Connected as alice@example.com — switch account".
    """
    status = {svc: {"connected": False} for svc in VALID_SERVICES}

    try:
        sb = get_supabase()
        # Try the new column first; fall back to the older shape if migration
        # 031 hasn't been applied to this environment yet.
        try:
            result = (
                sb.table("google_connections")
                .select("service, connected_at, account_email")
                .eq("tenant_id", tenant_id)
                .execute()
            )
        except Exception:
            result = (
                sb.table("google_connections")
                .select("service, connected_at")
                .eq("tenant_id", tenant_id)
                .execute()
            )
        for row in result.data or []:
            svc = row.get("service")
            if svc in status:
                status[svc] = {
                    "connected": True,
                    "connected_at": row.get("connected_at"),
                    "account_email": row.get("account_email"),
                }
    except Exception as exc:
        logger.error(f"google_status error: {exc}")

    return status
