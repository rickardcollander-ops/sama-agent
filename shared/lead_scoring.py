"""
Lead Scoring Engine
Scores leads based on ICP fit and behavioral signals.

Score ranges:
  0-39:  Cold — passive nurture list
  40-69: Warm — active nurture sequence
  70-100: Hot (Sales Qualified) — push to CRM + urgent notification
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ── ICP Fit Scoring (0-50 points) ───────────────────────────────────────────

# Company size signals (from company name / domain patterns)
HIGH_VALUE_KEYWORDS = ["saas", "software", "platform", "tech", "cloud", "ai", "data"]
MEDIUM_VALUE_KEYWORDS = ["agency", "consulting", "digital", "marketing", "growth"]

# UTM source value
SOURCE_SCORES = {
    "google": 10,       # Paid/organic search — high intent
    "linkedin": 8,      # Professional network
    "twitter": 5,
    "facebook": 3,
    "reddit": 4,
    "email": 12,        # Responded to nurture — very engaged
    "direct": 6,        # Typed URL directly
    "referral": 10,
}


def _score_icp_fit(lead: Dict[str, Any]) -> int:
    """Score based on Ideal Customer Profile fit (0-50)."""
    score = 0
    company = (lead.get("company") or "").lower()

    # Company provided = engaged (+10)
    if company:
        score += 10

    # Company keywords suggesting SaaS/tech
    for kw in HIGH_VALUE_KEYWORDS:
        if kw in company:
            score += 8
            break
    for kw in MEDIUM_VALUE_KEYWORDS:
        if kw in company:
            score += 5
            break

    # Has name (+5)
    if lead.get("name"):
        score += 5

    # Has phone (+10 — very high intent)
    if lead.get("phone"):
        score += 10

    # Has message (+7)
    if lead.get("message"):
        score += 7

    return min(score, 50)


def _score_behavior(lead: Dict[str, Any], touchpoints: list) -> int:
    """Score based on behavioral signals (0-50)."""
    score = 0

    # Source quality
    utm_source = (lead.get("utm_source") or "direct").lower()
    score += SOURCE_SCORES.get(utm_source, 3)

    # Came from comparison page = high intent (+15)
    source_url = (lead.get("source_url") or "").lower()
    if "/vs/" in source_url:
        score += 15
    elif "/pricing" in source_url:
        score += 12
    elif "/blog/" in source_url:
        score += 5

    # Number of touchpoints
    tp_count = len(touchpoints)
    if tp_count >= 5:
        score += 15
    elif tp_count >= 3:
        score += 10
    elif tp_count >= 2:
        score += 5

    # Has booking touchpoint
    for tp in touchpoints:
        if tp.get("touchpoint_type") == "booking_clicked":
            score += 10
            break

    return min(score, 50)


async def score_lead(lead_id: str) -> int:
    """
    Calculate total score for a lead.
    Returns score 0-100.
    """
    try:
        from shared.database import get_supabase
        sb = get_supabase()

        lead_result = sb.table("leads").select("*").eq("id", lead_id).execute()
        if not lead_result.data:
            return 0

        lead = lead_result.data[0]

        touchpoints_result = sb.table("lead_touchpoints").select("*").eq("lead_id", lead_id).execute()
        touchpoints = touchpoints_result.data or []

        icp_score = _score_icp_fit(lead)
        behavior_score = _score_behavior(lead, touchpoints)
        total = icp_score + behavior_score

        logger.info(f"Lead {lead_id} scored: {total} (ICP: {icp_score}, behavior: {behavior_score})")
        return total

    except Exception as e:
        logger.error(f"Lead scoring failed for {lead_id}: {e}")
        return 0


async def check_and_escalate(lead_id: str, score: int):
    """If lead score >= 70, escalate to CRM and notify."""
    if score < 70:
        return

    try:
        from shared.notifications import notification_service
        from shared.database import get_supabase

        sb = get_supabase()
        lead = sb.table("leads").select("email,company,name").eq("id", lead_id).execute()
        if not lead.data:
            return

        info = lead.data[0]
        sb.table("leads").update({"status": "qualified"}).eq("id", lead_id).execute()

        await notification_service.notify(
            title="Sales Qualified Lead!",
            message=f"{info.get('name', info['email'])} ({info.get('company', 'Unknown')}) — Score: {score}",
            severity="critical",
            agent="leads",
        )

        # Push to Growth Hub CRM if configured
        try:
            from shared.config import settings
            if settings.GROWTH_HUB_BRIDGE_API_KEY:
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{settings.LINKEDIN_AGENT_API_URL}/api/leads",
                        headers={"Authorization": f"Bearer {settings.GROWTH_HUB_BRIDGE_API_KEY}"},
                        json={
                            "email": info["email"],
                            "name": info.get("name", ""),
                            "company": info.get("company", ""),
                            "source": "sama",
                            "score": score,
                        },
                        timeout=10,
                    )
                logger.info(f"Lead {lead_id} pushed to Growth Hub CRM")
        except Exception as e:
            logger.warning(f"Growth Hub push failed: {e}")

    except Exception as e:
        logger.error(f"Lead escalation failed: {e}")
