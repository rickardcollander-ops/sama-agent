"""
Analytics Overview API Route
Cross-channel analytics overview for the dashboard.
"""

import logging
from fastapi import APIRouter, Request

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/overview")
async def analytics_overview(request: Request, days: int = 30):
    """
    Cross-channel analytics overview for the current tenant.
    Returns summary metrics, per-channel breakdown, and daily trends.
    Never returns null for numeric fields — always 0.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")

    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_ANALYTICS_OVERVIEW
        return DEMO_ANALYTICS_OVERVIEW

    # Default empty response — all zeros
    empty = {
        "period": f"last_{days}_days",
        "summary": {
            "total_sessions": 0,
            "total_pageviews": 0,
            "total_clicks": 0,
            "total_impressions": 0,
            "avg_position": 0.0,
            "bounce_rate": 0.0,
            "avg_session_duration": 0,
            "conversion_rate": 0.0,
            "total_conversions": 0,
        },
        "channels": {},
        "trends": [],
    }

    try:
        sb = get_supabase()
        result = (
            sb.table("daily_metrics")
            .select("*")
            .order("date", desc=True)
            .limit(days)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return empty

        total_sessions = sum((r.get("sessions") or 0) for r in rows)
        total_pageviews = sum((r.get("pageviews") or 0) for r in rows)
        total_clicks = sum((r.get("clicks") or 0) for r in rows)
        total_impressions = sum((r.get("impressions") or 0) for r in rows)
        positions = [r.get("avg_position") or 0 for r in rows if r.get("avg_position")]
        avg_position = round(sum(positions) / len(positions), 1) if positions else 0.0
        bounce_rates = [r.get("bounce_rate") or 0 for r in rows if r.get("bounce_rate")]
        avg_bounce = round(sum(bounce_rates) / len(bounce_rates), 1) if bounce_rates else 0.0
        total_conversions = sum((r.get("conversions") or 0) for r in rows)
        conversion_rate = round(total_conversions / total_sessions * 100, 2) if total_sessions else 0.0

        return {
            "period": f"last_{days}_days",
            "summary": {
                "total_sessions": total_sessions,
                "total_pageviews": total_pageviews,
                "total_clicks": total_clicks,
                "total_impressions": total_impressions,
                "avg_position": avg_position,
                "bounce_rate": avg_bounce,
                "avg_session_duration": 0,
                "conversion_rate": conversion_rate,
                "total_conversions": total_conversions,
            },
            "channels": {},
            "trends": [
                {
                    "date": r.get("date", ""),
                    "sessions": r.get("sessions") or 0,
                    "clicks": r.get("clicks") or 0,
                    "impressions": r.get("impressions") or 0,
                    "conversions": r.get("conversions") or 0,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.error(f"analytics_overview error: {e}")
        return empty
