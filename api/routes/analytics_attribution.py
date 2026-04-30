"""
Cross-channel attribution, ROI, and weekly reports.

Builds a unified view across SEO clicks, social engagement, ads spend, and
content publication using rows the other agents have already written.

Attribution model: simple last-touch by source field on conversions/leads,
falling back to channel-share-of-clicks when conversions aren't tracked.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _safe_table(sb, name: str, tenant_id: str, since: str, ts_col: str = "created_at") -> List[dict]:
    """Read rows for a tenant from a possibly-missing table without raising."""
    try:
        q = sb.table(name).select("*").eq("tenant_id", tenant_id)
        if ts_col:
            q = q.gte(ts_col, since)
        return q.execute().data or []
    except Exception as e:
        logger.debug(f"_safe_table({name}) failed: {e}")
        return []


# ── /attribution — channel breakdown ────────────────────────────────────────

@router.get("/attribution")
async def attribution(request: Request, days: int = 30):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    since = _since(days)

    seo = _safe_table(sb, "daily_metrics", tenant_id, since, ts_col="date")
    social = _safe_table(sb, "social_posts", tenant_id, since)
    ads = _safe_table(sb, "ad_creatives", tenant_id, since)
    leads = _safe_table(sb, "leads", tenant_id, since)
    content = _safe_table(sb, "content_pieces", tenant_id, since)

    seo_clicks = sum((r.get("clicks") or 0) for r in seo)
    social_clicks = sum(
        (p.get("likes", 0) or 0) + (p.get("shares", 0) or 0) for p in social
    )
    ads_clicks = sum((a.get("clicks") or 0) for a in ads)
    ads_spend = sum(float(a.get("spend") or 0) for a in ads)

    # Last-touch attribution from leads.source
    lead_source_counts: Dict[str, int] = defaultdict(int)
    for lead in leads:
        src = (lead.get("source") or "unknown").lower()
        lead_source_counts[src] += 1

    total_clicks = seo_clicks + social_clicks + ads_clicks or 1
    channels = {
        "seo": {
            "clicks": seo_clicks,
            "share": round(seo_clicks / total_clicks * 100, 1),
            "leads": lead_source_counts.get("seo", 0)
            + lead_source_counts.get("organic", 0),
            "spend": 0.0,
        },
        "social": {
            "clicks": social_clicks,
            "share": round(social_clicks / total_clicks * 100, 1),
            "leads": lead_source_counts.get("social", 0)
            + lead_source_counts.get("twitter", 0)
            + lead_source_counts.get("linkedin", 0),
            "spend": 0.0,
        },
        "ads": {
            "clicks": ads_clicks,
            "share": round(ads_clicks / total_clicks * 100, 1),
            "leads": lead_source_counts.get("ads", 0)
            + lead_source_counts.get("paid", 0)
            + lead_source_counts.get("google_ads", 0),
            "spend": round(ads_spend, 2),
        },
    }

    return {
        "period_days": days,
        "channels": channels,
        "totals": {
            "clicks": total_clicks if total_clicks > 1 else 0,
            "leads": sum(lead_source_counts.values()),
            "ads_spend": round(ads_spend, 2),
            "content_published": len(
                [c for c in content if c.get("status") == "published"]
            ),
        },
    }


# ── /roi — return-on-investment summary ─────────────────────────────────────

@router.get("/roi")
async def roi(request: Request, days: int = 30, lead_value: float = 100.0):
    """
    Compute a back-of-envelope ROI. ``lead_value`` is the average revenue per
    lead — defaults to $100 if the tenant hasn't supplied one in settings.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    since = _since(days)

    # Allow tenants to override lead_value via user_settings
    try:
        ts = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        cfg = (ts.data or {}).get("settings", {}) if ts.data else {}
        if cfg.get("lead_value"):
            lead_value = float(cfg["lead_value"])
    except Exception:
        pass

    leads = _safe_table(sb, "leads", tenant_id, since)
    ads = _safe_table(sb, "ad_creatives", tenant_id, since)

    total_leads = len(leads)
    estimated_revenue = total_leads * lead_value
    ads_spend = sum(float(a.get("spend") or 0) for a in ads)

    # Add an estimated content + social cost ($0 by default; tenant can override)
    other_spend = 0.0
    total_spend = ads_spend + other_spend
    roi_pct = (
        round((estimated_revenue - total_spend) / total_spend * 100, 1)
        if total_spend > 0
        else None
    )

    return {
        "period_days": days,
        "leads": total_leads,
        "lead_value": lead_value,
        "estimated_revenue": round(estimated_revenue, 2),
        "ads_spend": round(ads_spend, 2),
        "total_spend": round(total_spend, 2),
        "roi_pct": roi_pct,
    }


# ── /weekly-report — generate a markdown summary for the last 7 days ────────

@router.get("/weekly-report")
async def weekly_report(request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    if settings.DEMO_MODE:
        return {
            "period": "last_7_days",
            "markdown": "# Weekly report\n\nDemo mode — connect your accounts to see real data.",
        }

    since = _since(7)
    seo = _safe_table(sb, "daily_metrics", tenant_id, since, ts_col="date")
    social = _safe_table(sb, "social_posts", tenant_id, since)
    content = _safe_table(sb, "content_pieces", tenant_id, since)
    leads = _safe_table(sb, "leads", tenant_id, since)
    ads = _safe_table(sb, "ad_creatives", tenant_id, since)

    seo_clicks = sum((r.get("clicks") or 0) for r in seo)
    seo_impressions = sum((r.get("impressions") or 0) for r in seo)
    new_content = [c for c in content if c.get("status") == "published"]
    posts_count = len([p for p in social if p.get("status") in ("published", "published_locally")])

    md_lines = [
        f"# Weekly report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        "## SEO",
        f"- Clicks: **{seo_clicks:,}**",
        f"- Impressions: **{seo_impressions:,}**",
        "",
        "## Content",
        f"- New pieces published: **{len(new_content)}**",
        *[f"  - {c.get('title', 'Untitled')}" for c in new_content[:5]],
        "",
        "## Social",
        f"- Posts published: **{posts_count}**",
        "",
        "## Ads",
        f"- Active creatives: **{len(ads)}**",
        f"- Total spend: **${sum(float(a.get('spend') or 0) for a in ads):,.2f}**",
        "",
        "## Leads",
        f"- New leads: **{len(leads)}**",
    ]

    return {
        "period": "last_7_days",
        "markdown": "\n".join(md_lines),
        "summary": {
            "seo_clicks": seo_clicks,
            "seo_impressions": seo_impressions,
            "content_published": len(new_content),
            "posts_published": posts_count,
            "leads": len(leads),
        },
    }
