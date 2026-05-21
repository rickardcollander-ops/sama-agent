"""
Google Search Console integration routes — site discovery + selection.

Mirrors the shape of google_analytics.py so the dashboard's
GoogleSearchConsolePropertyPicker component can work the same way as
the GA4 picker: list all accessible sites, let the user choose one,
and save it to user_settings.gsc_site_url.
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


async def _get_selected_site_url(tenant_id: str) -> Optional[str]:
    sb = get_supabase()
    try:
        res = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        if res.data:
            return (res.data.get("settings") or {}).get("gsc_site_url")
    except Exception:
        pass
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
    sites: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{GSC_API}/sites", headers=headers)

        if resp.status_code == 403:
            who = f" ({connected_email})" if connected_email else ""
            raise HTTPException(
                status_code=403,
                detail=(
                    f"The connected Google account{who} doesn't have access to any "
                    "Search Console properties. Switch to a Google account that owns "
                    "or has been granted access to the site."
                ),
            )
        if resp.status_code != 200:
            logger.warning(f"GSC sites {resp.status_code}: {resp.text[:200]}")
            raise HTTPException(
                status_code=502,
                detail=f"Search Console API error {resp.status_code}",
            )

        data = resp.json()
        for entry in data.get("siteEntry", []):
            site_url = entry.get("siteUrl", "")
            if site_url:
                sites.append(
                    {
                        "url": site_url,
                        "permission_level": entry.get("permissionLevel"),
                    }
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GSC sites fetch failed for {tid}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch sites: {e}")

    selected = await _get_selected_site_url(tid)

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
    """Save the chosen Search Console site URL to user_settings."""
    tid = _tenant_id(request)
    if not payload.site_url.strip():
        raise HTTPException(status_code=400, detail="site_url is required")

    sb = get_supabase()

    try:
        existing = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tid)
            .single()
            .execute()
        )
        current_settings = (existing.data or {}).get("settings", {}) if existing.data else {}
    except Exception:
        current_settings = {}

    current_settings["gsc_site_url"] = payload.site_url.strip()

    try:
        sb.table("user_settings").upsert(
            {
                "user_id": tid,
                "settings": current_settings,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        logger.error(f"select-site save failed for {tid}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save site: {e}")

    invalidate_tenant_cache(tid)

    return {
        "success": True,
        "tenant_id": tid,
        "site_url": payload.site_url.strip(),
    }
