"""
Google Analytics integration routes — GA4 property discovery + selection.

The frontend OAuth flow stores tokens in ``google_connections``. To actually
pull data the analytics agent needs to know **which** GA4 property to query.
These endpoints let the dashboard:

1. List GA4 properties accessible to the connected Google account
   (``GET /properties``).
2. Save the chosen property so the tenant-scoped analytics agent picks it up
   (``POST /select-property``).
3. Read the currently selected property (``GET /selected-property``).

Storage routing for the selected GA4 property mirrors the dashboard's
``getTenantSettingsAccess`` logic:
- Primary/legacy site (site_id == account_id): ``user_settings.settings``
- Secondary site  (site_id != account_id): ``user_sites.settings``

This is required because ``user_settings.user_id`` references ``auth.users``
via a FK; using a site UUID there causes a 23503 violation.
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

ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"


def _tenant_id(request: Request, fallback: Optional[str] = None) -> str:
    return getattr(request.state, "tenant_id", None) or fallback or "default"


def _extract_google_error(resp: httpx.Response) -> Optional[str]:
    try:
        body = resp.json()
    except Exception:
        return None
    err = body.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return None


@router.get("/properties")
async def list_ga4_properties(request: Request, tenant_id: Optional[str] = Query(None)):
    """List GA4 properties accessible to the tenant's connected Google account.

    Uses the Analytics Admin API ``accountSummaries`` endpoint, which returns
    all accounts + their property summaries in a single call.
    """
    tid = _tenant_id(request, tenant_id)

    try:
        access_token = await get_google_access_token(tid, "analytics")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"GA4 properties: token error for {tid}: {e}")
        raise HTTPException(status_code=500, detail="Could not refresh Google token")

    connected_email = _get_connected_email(tid)
    headers = {"Authorization": f"Bearer {access_token}"}
    properties: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            page_token: Optional[str] = None
            while True:
                params = {"pageSize": 200}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(
                    f"{ADMIN_API}/accountSummaries",
                    headers=headers,
                    params=params,
                )
                if resp.status_code == 403:
                    who = f" ({connected_email})" if connected_email else ""
                    google_msg = _extract_google_error(resp)
                    # A 403 from accountSummaries can mean either "no GA4
                    # access" or "Analytics Admin API not enabled in this GCP
                    # project". Surface Google's own message so the dashboard
                    # can tell which it is instead of always blaming permissions.
                    if google_msg and ("has not been used" in google_msg or "is disabled" in google_msg):
                        raise HTTPException(
                            status_code=403,
                            detail=(
                                "Google Analytics Admin API is not enabled in the "
                                "Google Cloud project that owns the OAuth client. "
                                "Enable it at "
                                "https://console.cloud.google.com/apis/library/"
                                "analyticsadmin.googleapis.com and retry. "
                                f"(Google said: {google_msg})"
                            ),
                        )
                    extra = f" (Google said: {google_msg})" if google_msg else ""
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"The connected Google account{who} doesn't have access to any "
                            "GA4 properties. Either switch to a Google account that has "
                            "access, or have someone in Google Analytics grant this "
                            f"account at least Viewer access on the property.{extra}"
                        ),
                    )
                if resp.status_code != 200:
                    logger.warning(f"accountSummaries {resp.status_code}: {resp.text[:200]}")
                    google_msg = _extract_google_error(resp)
                    extra = f": {google_msg}" if google_msg else ""
                    raise HTTPException(
                        status_code=502,
                        detail=f"Google Analytics Admin API error {resp.status_code}{extra}",
                    )
                data = resp.json()
                for account in data.get("accountSummaries", []):
                    account_name = account.get("displayName", "Unknown account")
                    account_id = account.get("account", "")
                    for prop in account.get("propertySummaries", []):
                        # `prop["property"]` looks like "properties/123456".
                        prop_resource = prop.get("property", "")
                        prop_id = prop_resource.split("/")[-1] if prop_resource else ""
                        properties.append({
                            "property_id": prop_id,
                            "property_resource": prop_resource,
                            "display_name": prop.get("displayName", "Unnamed property"),
                            "parent_account": account_name,
                            "account_id": account_id,
                        })
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GA4 properties fetch failed for {tid}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch properties: {e}")

    req_account_id = getattr(request.state, "account_id", None) or tid
    req_site_id = getattr(request.state, "site_id", None) or tid
    selected = await _get_selected_property_id(tid, account_id=req_account_id, site_id=req_site_id)

    return {
        "tenant_id": tid,
        "connected_account_email": connected_email,
        "selected_property_id": selected,
        "properties": properties,
        "count": len(properties),
    }


class SelectPropertyPayload(BaseModel):
    property_id: str
    display_name: Optional[str] = None


@router.post("/select-property")
async def select_ga4_property(payload: SelectPropertyPayload, request: Request):
    """Save the chosen GA4 property so the analytics agent uses it.

    Routes to user_sites.settings for secondary sites (site_id != account_id)
    and user_settings for the primary/legacy site, mirroring the dashboard's
    getTenantSettingsAccess logic. This avoids a FK violation that occurred when
    a site UUID (not an auth user UUID) was used as user_settings.user_id.
    """
    tid = _tenant_id(request)
    account_id: str = getattr(request.state, "account_id", None) or tid
    site_id: str = getattr(request.state, "site_id", None) or tid

    if not payload.property_id.strip():
        raise HTTPException(status_code=400, detail="property_id is required")

    # Normalise: strip "properties/" prefix if present — TenantConfig.ga4_property_id
    # expects the bare numeric id, and the analytics agent re-adds the prefix.
    prop_id = payload.property_id.strip()
    if prop_id.startswith("properties/"):
        prop_id = prop_id.split("/", 1)[1]

    sb = get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()

    if site_id != account_id:
        # Secondary site: write to user_sites.settings.
        # user_settings.user_id is a FK on auth.users — a site UUID would violate it.
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

        current_settings["ga4_property_id"] = prop_id
        if payload.display_name:
            current_settings["ga4_property_name"] = payload.display_name

        try:
            sb.table("user_sites").update(
                {"settings": current_settings, "updated_at": now_iso}
            ).eq("id", site_id).execute()
        except Exception as e:
            logger.error(f"select-property save failed (user_sites site={site_id}): {e}")
            raise HTTPException(status_code=500, detail=f"Could not save property: {e}")
    else:
        # Primary/legacy site: write to user_settings keyed by auth user UUID.
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

        current_settings["ga4_property_id"] = prop_id
        if payload.display_name:
            current_settings["ga4_property_name"] = payload.display_name

        try:
            sb.table("user_settings").upsert(
                {"user_id": account_id, "settings": current_settings, "updated_at": now_iso},
                on_conflict="user_id",
            ).execute()
        except Exception as e:
            logger.error(f"select-property save failed (user_settings user={account_id}): {e}")
            raise HTTPException(status_code=500, detail=f"Could not save property: {e}")

    # Drop cached TenantConfig so next request reads the fresh value.
    invalidate_tenant_cache(tid, account_id=account_id, site_id=site_id if site_id != account_id else None)

    return {
        "success": True,
        "tenant_id": tid,
        "property_id": prop_id,
        "display_name": payload.display_name,
    }


@router.get("/selected-property")
async def get_selected_property(request: Request, tenant_id: Optional[str] = Query(None)):
    tid = _tenant_id(request, tenant_id)
    req_account_id = getattr(request.state, "account_id", None) or tid
    req_site_id = getattr(request.state, "site_id", None) or tid
    return {
        "tenant_id": tid,
        "property_id": await _get_selected_property_id(tid, account_id=req_account_id, site_id=req_site_id),
    }


async def _get_selected_property_id(
    tenant_id: str,
    *,
    account_id: Optional[str] = None,
    site_id: Optional[str] = None,
) -> Optional[str]:
    """Read the selected GA4 property, checking user_sites first then user_settings."""
    from shared.tenant import get_tenant_config
    _account_id = account_id or tenant_id
    _site_id = site_id or tenant_id
    try:
        config = await get_tenant_config(
            tenant_id,
            account_id=_account_id,
            site_id=_site_id,
        )
        prop = config.ga4_property_id
        return prop if prop else None
    except Exception:
        pass
    return None


def _get_connected_email(tenant_id: str) -> Optional[str]:
    """Best-effort lookup of the Google email the analytics service is linked to.

    Returns None if migration 031 hasn't been applied or the row is missing.
    """
    try:
        sb = get_supabase()
        res = (
            sb.table("google_connections")
            .select("account_email")
            .eq("tenant_id", tenant_id)
            .eq("service", "analytics")
            .single()
            .execute()
        )
        if res.data:
            email = res.data.get("account_email")
            return email if isinstance(email, str) and email else None
    except Exception:
        pass
    return None
