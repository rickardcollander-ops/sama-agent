"""
Meta Ads API Routes
Campaign management, performance reporting, and AI insights for Meta (Facebook/Instagram).

All routes sit under /api/ads/meta/* (registered in main.py with prefix=/api/ads).
"""

import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.ads_meta import meta_ads_agent

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Status / Account ────────────────────────────────────────────────────────

@router.get("/meta/status")
async def meta_status(request: Request):
    """Check Meta Ads connection status and account info."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        from shared.database import get_supabase
        sb = get_supabase()
        result = (
            sb.table("ad_platform_credentials")
            .select("access_token, account_id, is_connected, connected_at")
            .eq("tenant_id", tenant_id)
            .eq("platform", "meta")
            .execute()
        )
        row = result.data[0] if result.data else None
        if not row or not row.get("is_connected") or not row.get("access_token"):
            return {"connected": False, "platform": "meta"}

        verified = await meta_ads_agent.verify_credentials(
            row["access_token"], row["account_id"]
        )
        return {
            "connected": verified.get("valid", False),
            "platform": "meta",
            "account_id": row.get("account_id"),
            "account_name": verified.get("account_name", ""),
            "currency": verified.get("currency", ""),
            "timezone": verified.get("timezone", ""),
            "connected_at": row.get("connected_at"),
            "error": verified.get("error") if not verified.get("valid") else None,
        }
    except Exception as e:
        logger.error(f"meta_status error: {e}")
        return {"connected": False, "platform": "meta", "error": str(e)}


# ── Campaigns ────────────────────────────────────────────────────────────────

@router.get("/meta/campaigns")
async def get_meta_campaigns(request: Request, date_range: int = 30):
    """List Meta campaigns with aggregated performance insights."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        data = await meta_ads_agent.get_performance_summary(tenant_id, date_range)
        return data
    except Exception as e:
        logger.error(f"get_meta_campaigns error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/meta/campaigns/{campaign_id}/adsets")
async def get_campaign_adsets(request: Request, campaign_id: str, date_range: int = 30):
    """Get ad sets for a campaign with performance data."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        adsets = await meta_ads_agent.get_adsets(tenant_id, campaign_id, date_range)
        return {"adsets": adsets}
    except Exception as e:
        logger.error(f"get_campaign_adsets error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Campaign Actions ─────────────────────────────────────────────────────────

@router.post("/meta/campaigns/{campaign_id}/pause")
async def pause_meta_campaign(request: Request, campaign_id: str):
    """Pause a Meta campaign."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    result = await meta_ads_agent.set_campaign_status(tenant_id, campaign_id, "PAUSED")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to pause campaign"))
    return result


@router.post("/meta/campaigns/{campaign_id}/resume")
async def resume_meta_campaign(request: Request, campaign_id: str):
    """Resume (activate) a Meta campaign."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    result = await meta_ads_agent.set_campaign_status(tenant_id, campaign_id, "ACTIVE")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to resume campaign"))
    return result


class BudgetUpdate(BaseModel):
    daily_budget: float  # in account currency (e.g. SEK or USD)


@router.post("/meta/campaigns/{campaign_id}/budget")
async def update_meta_budget(request: Request, campaign_id: str, payload: BudgetUpdate):
    """Update a campaign's daily budget."""
    if payload.daily_budget < 1:
        raise HTTPException(status_code=400, detail="Budget must be at least 1")
    tenant_id = getattr(request.state, "tenant_id", "default")
    result = await meta_ads_agent.update_campaign_budget(tenant_id, campaign_id, payload.daily_budget)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to update budget"))
    return result


# ── AI Insights ──────────────────────────────────────────────────────────────

@router.get("/meta/insights")
async def get_meta_insights(request: Request):
    """Generate Claude-powered insights for Meta campaign performance."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        from shared.config import settings
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        result = await meta_ads_agent.generate_ai_insights(tenant_id, anthropic_client=client)
        return result
    except Exception as e:
        logger.error(f"get_meta_insights error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Credential Verification ───────────────────────────────────────────────────

@router.post("/meta/verify")
async def verify_meta_credentials(request: Request):
    """Verify stored Meta credentials against the Marketing API."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        from shared.database import get_supabase
        sb = get_supabase()
        result = (
            sb.table("ad_platform_credentials")
            .select("access_token, account_id")
            .eq("tenant_id", tenant_id)
            .eq("platform", "meta")
            .execute()
        )
        if not result.data:
            return {"verified": False, "reason": "No credentials stored"}
        row = result.data[0]
        if not row.get("access_token"):
            return {"verified": False, "reason": "No access token stored"}
        verified = await meta_ads_agent.verify_credentials(
            row["access_token"], row["account_id"]
        )
        if verified.get("valid"):
            # Mark as connected in DB
            from datetime import datetime, timezone
            sb.table("ad_platform_credentials").update({
                "is_connected": True,
                "connected_at": datetime.now(timezone.utc).isoformat(),
            }).eq("tenant_id", tenant_id).eq("platform", "meta").execute()
        return {"verified": verified.get("valid", False), **verified}
    except Exception as e:
        logger.error(f"verify_meta_credentials error: {e}")
        return {"verified": False, "reason": str(e)}
