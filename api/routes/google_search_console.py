"""
Google Search Console integration routes — site discovery + selection.

The frontend OAuth flow stores tokens in ``google_connections``. To pull
data the SEO agent needs to know **which** GSC site property to query.
These endpoints let the dashboard:

1. List GSC sites accessible to the connected Google account
   (``GET /sites``).
2. Save the chosen site so the tenant-scoped SEO agent picks it up
   (``POST /select-property``).

Storage routing mirrors the GA4 property picker (google_analytics.py):
- Secondary site (site_id != account_id): user_sites.settings
- Primary/legacy site (site_id == account_id): user_settings.settings
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from shared.database import get_supabase
from shared.google_auth import get_google_access_token
from shared.tenant import invalidate_tenant_cache

router = APIRouter()
logger = logging.getLogger(__name__)

GSC_API = "https://www.googleapis.com/webmasters/v3"


def _tenant_id(request: Request, fallback: Optional[str] = None) -> str:
    return getattr(request.state, "tenant_id", None) or fallback or "default"


def _get_connected_email(tenant_id: str) -> Optional[str]:
    """Look up the Google account email linked to the GSC service."""
    try:
        sb = get_supabase()
        res = (
            sb.table("google_connections")
            .select("account_email")
            .eq("tenant_id", tenant_id)
            .eq("service", "search_console")
            .single()
            .execute()
        )
        if res.data:
            email = res.data.get("account_email")
            return email if isinstance(email, str) and email else None
    except Exception:
        pass
    return None


async def _get_selected_site_url(tenant_id: str, *, account_id: Optional[str] = None, site_id: Optional[str] = None) -> Optional[str]:
    from shared.tenant import get_tenant_config
    try:
        config = await get_tenant_config(
            tenant_id,
            account_id=account_id or tenant_id,
            site_id=site_id or tenant_id,
        )
        url = config.gsc_site_url
        return url if url else None
    except Exception:
        return None


@router.get("/sites")
async def list_gsc_sites(request: Request, tenant_id: Optional[str] = Query(None)):
    """List Search Console sites accessible to the tenant's connected Google account."""
    tid = _tenant_id(request, tenant_id)

    try:
        access_token = await get_google_access_token(tid, "search_console")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"GSC sites: token error for {tid}: {e}")
        raise HTTPException(status_code=500, detail="Could not refresh Google token")

    connected_email = _get_connected_email(tid)
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{GSC_API}/sites", headers=headers)

        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Google token is invalid or expired. Reconnect Search Console.")

        if resp.status_code == 403:
            who = f" ({connected_email})" if connected_email else ""
            raise HTTPException(
                status_code=403,
                detail=(
                    f"The connected Google account{who} doesn't have access to any "
                    "Search Console properties. Either switch to a Google account that has "
                    "access, or verify site ownership in Search Console."
                ),
            )

        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Google Search Console API error {resp.status_code}",
            )

        data = resp.json()
        sites: List[Dict[str, Any]] = [
            {
                "url": entry["siteUrl"],
                "permission_level": entry.get("permissionLevel", ""),
            }
            for entry in data.get("siteEntry", [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GSC sites fetch failed for {tid}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch sites: {e}")

    req_account_id = getattr(request.state, "account_id", None) or tid
    req_site_id = getattr(request.state, "site_id", None) or tid
    selected = await _get_selected_site_url(tid, account_id=req_account_id, site_id=req_site_id)

    return {
        "tenant_id": tid,
        "connected_account_email": connected_email,
        "selected_site_url": selected,
        "sites": sites,
        "count": len(sites),
    }


class SelectSitePayload(BaseModel):
    site_url: str


@router.post("/select-property")
async def select_gsc_site(payload: SelectSitePayload, request: Request):
    """Save the chosen GSC site so the SEO agent uses it.

    Routes to user_sites.settings for secondary sites (site_id != account_id)
    and user_settings for the primary/legacy site, mirroring the GA4 picker.
    """
    tid = _tenant_id(request)
    account_id: str = getattr(request.state, "account_id", None) or tid
    site_id: str = getattr(request.state, "site_id", None) or tid

    site_url = payload.site_url.strip()
    if not site_url:
        raise HTTPException(status_code=400, detail="site_url is required")

    sb = get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()

    if site_id != account_id:
        try:
            existing = (
                sb.table("user_sites")
                .select("settings")
                .eq("id", site_id)
                .maybeSingle()
                .execute()
            )
            current_settings = (existing.data or {}).get("settings", {}) if existing.data else {}
        except Exception:
            current_settings = {}

        current_settings["gsc_site_url"] = site_url

        try:
            sb.table("user_sites").update(
                {"settings": current_settings, "updated_at": now_iso}
            ).eq("id", site_id).execute()
        except Exception as e:
            logger.error(f"select-property save failed (user_sites site={site_id}): {e}")
            raise HTTPException(status_code=500, detail=f"Could not save site: {e}")
    else:
        try:
            existing = (
                sb.table("user_settings")
                .select("settings")
                .eq("user_id", account_id)
                .maybeSingle()
                .execute()
            )
            current_settings = (existing.data or {}).get("settings", {}) if existing.data else {}
        except Exception:
            current_settings = {}

        current_settings["gsc_site_url"] = site_url

        try:
            sb.table("user_settings").upsert(
                {"user_id": account_id, "settings": current_settings, "updated_at": now_iso},
                on_conflict="user_id",
            ).execute()
        except Exception as e:
            logger.error(f"select-property save failed (user_settings user={account_id}): {e}")
            raise HTTPException(status_code=500, detail=f"Could not save site: {e}")

    invalidate_tenant_cache(tid, account_id=account_id, site_id=site_id if site_id != account_id else None)

    return {"success": True, "tenant_id": tid, "site_url": site_url}
