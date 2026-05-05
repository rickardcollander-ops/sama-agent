"""
Google Analytics integration routes — GA4 property discovery + selection.

The frontend OAuth flow stores tokens in ``google_connections``. To actually
pull data the analytics agent needs to know **which** GA4 property to query.
These endpoints let the dashboard:

1. List GA4 properties accessible to the connected Google account
   (``GET /properties``).
2. Save the chosen property to ``user_settings.ga4_property_id`` so the
   tenant-scoped analytics agent picks it up (``POST /select-property``).
3. Read the currently selected property (``GET /selected-property``).
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
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"The connected Google account{who} doesn't have access to any "
                            "GA4 properties. Either switch to a Google account that has "
                            "access, or have someone in Google Analytics grant this "
                            "account at least Viewer access on the property."
                        ),
                    )
                if resp.status_code != 200:
                    logger.warning(f"accountSummaries {resp.status_code}: {resp.text[:200]}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Google Analytics Admin API error {resp.status_code}",
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

    selected = await _get_selected_property_id(tid)

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
    """Save the chosen GA4 property to user_settings so the analytics agent uses it."""
    tid = _tenant_id(request)
    if not payload.property_id.strip():
        raise HTTPException(status_code=400, detail="property_id is required")

    # Normalise: strip "properties/" prefix if present — TenantConfig.ga4_property_id
    # expects the bare numeric id, and the analytics agent re-adds the prefix.
    prop_id = payload.property_id.strip()
    if prop_id.startswith("properties/"):
        prop_id = prop_id.split("/", 1)[1]

    sb = get_supabase()

    # Merge into existing settings rather than overwriting.
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

    current_settings["ga4_property_id"] = prop_id
    if payload.display_name:
        current_settings["ga4_property_name"] = payload.display_name

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
        logger.error(f"select-property save failed: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save property: {e}")

    # Drop cached TenantConfig so next request reads the fresh value.
    invalidate_tenant_cache(tid)

    return {
        "success": True,
        "tenant_id": tid,
        "property_id": prop_id,
        "display_name": payload.display_name,
    }


@router.get("/selected-property")
async def get_selected_property(request: Request, tenant_id: Optional[str] = Query(None)):
    tid = _tenant_id(request, tenant_id)
    return {
        "tenant_id": tid,
        "property_id": await _get_selected_property_id(tid),
    }


async def _get_selected_property_id(tenant_id: str) -> Optional[str]:
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
            return (res.data.get("settings") or {}).get("ga4_property_id")
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
