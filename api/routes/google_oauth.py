"""
Google OAuth Routes
OAuth2 flows for connecting Google Search Console, Analytics GA4, and Ads.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

DASHBOARD_SETTINGS_URL = "https://sama-dashboard-alpha.vercel.app/c/settings"

SERVICE_SCOPES = {
    "search_console": "https://www.googleapis.com/auth/webmasters.readonly",
    "analytics": "https://www.googleapis.com/auth/analytics.readonly",
    "ads": "https://www.googleapis.com/auth/adwords",
}

VALID_SERVICES = set(SERVICE_SCOPES.keys())


def _redirect_uri() -> str:
    return settings.GOOGLE_OAUTH_REDIRECT_URI


# ── Connect: redirect to Google consent screen ─────────────────────────────

@router.get("/connect")
async def google_connect(
    service: str = Query(..., description="One of: search_console, analytics, ads"),
    tenant_id: str = Query("default"),
):
    """Generate Google OAuth URL and redirect user to consent screen."""
    if service not in VALID_SERVICES:
        raise HTTPException(status_code=400, detail=f"Invalid service '{service}'. Must be one of {sorted(VALID_SERVICES)}")

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth credentials not configured on server")

    state = json.dumps({"tenant_id": tenant_id, "service": service})

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SERVICE_SCOPES[service],
        "access_type": "offline",
        "prompt": "consent",
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
    """Handle OAuth callback from Google, store tokens, redirect to dashboard."""

    if error:
        logger.warning(f"Google OAuth error: {error}")
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error={error}")

    if not code or not state:
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=missing_params")

    # Decode state
    try:
        state_data = json.loads(state)
        tenant_id = state_data["tenant_id"]
        service = state_data["service"]
    except (json.JSONDecodeError, KeyError):
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=invalid_state")

    if service not in VALID_SERVICES:
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=invalid_service")

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
            return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=token_exchange_failed")

        token_data = resp.json()
    except Exception as exc:
        logger.error(f"Google token exchange exception: {exc}")
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=token_exchange_error")

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token:
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=no_access_token")

    token_expiry = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

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
        sb.table("google_connections").upsert(row, on_conflict="tenant_id,service").execute()
        logger.info(f"Google {service} connected for tenant {tenant_id}")
    except Exception as exc:
        logger.error(f"Failed to store Google tokens: {exc}")
        return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_error=storage_failed")

    return RedirectResponse(url=f"{DASHBOARD_SETTINGS_URL}?google_connected={service}")


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
    """Return connection status for each Google service."""
    status = {svc: {"connected": False} for svc in VALID_SERVICES}

    try:
        sb = get_supabase()
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
                }
    except Exception as exc:
        logger.error(f"google_status error: {exc}")

    return status
