"""
Ads Credentials API Routes
Manage ad platform credentials (Meta, LinkedIn, Google) per tenant.
Tokens are masked in GET responses.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class CredentialSave(BaseModel):
    platform: str  # google | meta | linkedin
    access_token: Optional[str] = None
    account_id: Optional[str] = None


def _mask_token(token: Optional[str]) -> Optional[str]:
    """Mask all but last 4 characters of a token."""
    if not token or len(token) < 8:
        return "****" if token else None
    return "****" + token[-4:]


def _safe_row(row: dict) -> dict:
    """Mask sensitive fields before returning."""
    row["access_token"] = _mask_token(row.get("access_token"))
    return row


# ── Save credentials ────────────────────────────────────────────────────────

@router.post("/credentials")
async def save_ad_credentials(request: Request, payload: CredentialSave):
    """Save ad platform credentials for the tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        data = {
            "tenant_id": tenant_id,
            "platform": payload.platform,
            "access_token": payload.access_token,
            "account_id": payload.account_id,
            "is_connected": bool(payload.access_token),
            "connected_at": datetime.now(timezone.utc).isoformat() if payload.access_token else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # Upsert by tenant_id + platform
        result = sb.table("ad_platform_credentials").upsert(
            data,
            on_conflict="tenant_id,platform",
        ).execute()
        row = result.data[0] if result.data else data
        return {"success": True, "credential": _safe_row(row)}
    except Exception as e:
        logger.error(f"save_ad_credentials error: {e}")
        return {"success": False, "error": str(e)}


# ── Get connection status ────────────────────────────────────────────────────

@router.get("/credentials")
async def get_ad_credentials(request: Request):
    """Get connection status per platform (tokens masked)."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_AD_CREDENTIALS
        return {"credentials": DEMO_AD_CREDENTIALS}

    try:
        sb = get_supabase()
        result = (
            sb.table("ad_platform_credentials")
            .select("*")
            .eq("tenant_id", tenant_id)
            .execute()
        )
        rows = [_safe_row(r) for r in (result.data or [])]
        return {"credentials": rows}
    except Exception as e:
        logger.error(f"get_ad_credentials error: {e}")
        return {"credentials": []}


# ── Disconnect platform ──────────────────────────────────────────────────────

@router.delete("/credentials/{platform}")
async def disconnect_ad_platform(request: Request, platform: str):
    """Disconnect (delete credentials for) a platform."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("ad_platform_credentials").delete().eq("tenant_id", tenant_id).eq("platform", platform).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"disconnect_ad_platform error: {e}")
        return {"success": False, "error": str(e)}


# ── Verify connection ───────────────────────────────────────────────────────

@router.post("/credentials/verify/{platform}")
async def verify_ad_credential(request: Request, platform: str):
    """
    Test whether stored credentials for the given platform are valid.
    Currently returns a basic connectivity check; real verification
    would call each platform's API.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("ad_platform_credentials")
            .select("access_token, account_id, is_connected")
            .eq("tenant_id", tenant_id)
            .eq("platform", platform)
            .single()
            .execute()
        )
        if not result.data or not result.data.get("access_token"):
            return {"platform": platform, "verified": False, "reason": "No credentials stored"}

        # Placeholder: real implementation would call platform APIs
        return {"platform": platform, "verified": True, "reason": "Credentials present"}
    except Exception as e:
        logger.error(f"verify_ad_credential error: {e}")
        return {"platform": platform, "verified": False, "reason": str(e)}
