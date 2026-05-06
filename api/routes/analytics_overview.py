"""
Analytics Overview API Route
Cross-channel analytics overview for the dashboard.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


def _empty_response() -> Dict[str, Any]:
    return {
        "channels": [],
        "daily": [],
        "totals": {"clicks": 0, "impressions": 0, "conversions": 0, "spend": 0.0},
    }


def _sum_totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "clicks": sum((r.get("total_clicks") or 0) for r in rows),
        "impressions": sum((r.get("total_impressions") or 0) for r in rows),
        "conversions": int(sum((r.get("total_conversions") or 0) for r in rows)),
        "spend": round(float(sum((r.get("total_ad_spend") or 0) for r in rows)), 2),
    }


@router.get("/overview")
async def analytics_overview(request: Request, days: int = 30, compare: int = 0):
    """
    Cross-channel analytics overview for the current tenant.

    Reads aggregated rows from `daily_metrics` (populated by the analytics
    agent's `collect_daily_metrics` job) and shapes them into the format the
    customer dashboard expects: a `channels` array, a `daily` array, and a
    `totals` block. Pass `compare=1` to also receive `previous_totals` for
    the equivalent prior window.
    """
    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_ANALYTICS_OVERVIEW
        return DEMO_ANALYTICS_OVERVIEW

    days = max(1, min(int(days), 365))

    try:
        sb = get_supabase()
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=days - 1)

        result = (
            sb.table("daily_metrics")
            .select("*")
            .gte("date", start_date.isoformat())
            .lte("date", end_date.isoformat())
            .order("date", desc=False)
            .execute()
        )
        rows = result.data or []

        # Per-channel breakdown — sum each metric across the date range.
        channels_map: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            ch = r.get("channel") or "unknown"
            entry = channels_map.setdefault(ch, {
                "channel": ch,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0,
                "spend": 0.0,
            })
            entry["clicks"] += r.get("total_clicks") or 0
            entry["impressions"] += r.get("total_impressions") or 0
            entry["conversions"] += int(r.get("total_conversions") or 0)
            entry["spend"] += float(r.get("total_ad_spend") or 0)
        channels = sorted(
            ({**c, "spend": round(c["spend"], 2)} for c in channels_map.values()),
            key=lambda c: c["clicks"],
            reverse=True,
        )

        # Daily trend — collapse all channels per day.
        daily_map: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            date = r.get("date") or ""
            if not date:
                continue
            entry = daily_map.setdefault(date, {
                "date": date,
                "clicks": 0,
                "impressions": 0,
            })
            entry["clicks"] += r.get("total_clicks") or 0
            entry["impressions"] += r.get("total_impressions") or 0
        daily = sorted(daily_map.values(), key=lambda d: d["date"])

        response: Dict[str, Any] = {
            "channels": channels,
            "daily": daily,
            "totals": _sum_totals(rows),
        }

        if compare:
            prev_end = start_date - timedelta(days=1)
            prev_start = prev_end - timedelta(days=days - 1)
            prev_result = (
                sb.table("daily_metrics")
                .select("*")
                .gte("date", prev_start.isoformat())
                .lte("date", prev_end.isoformat())
                .execute()
            )
            response["previous_totals"] = _sum_totals(prev_result.data or [])

        return response
    except Exception as e:
        logger.error(f"analytics_overview error: {e}", exc_info=True)
        return _empty_response()
