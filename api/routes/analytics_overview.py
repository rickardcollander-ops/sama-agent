"""
Analytics Overview API Route
Cross-channel analytics overview for the dashboard.

Always fetches live data from every configured source so working channels
are always reflected in graphs — even when daily_metrics DB rows are missing
due to schema drift or failed upserts. Historical DB rows are used for the
daily time series when available; live data fills in the gaps.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from agents.analytics import AnalyticsAgent
from shared.config import settings
from shared.database import get_supabase
from shared.tenant import get_tenant_config

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


async def _safe_live(name: str, coro_factory) -> Dict[str, Any]:
    try:
        result = await coro_factory()
        return result if isinstance(result, dict) else {"status": "error"}
    except Exception as e:
        logger.warning(f"Live fetch {name} failed: {e}")
        return {"status": "error", "error": str(e)}


def _live_to_channel(channel: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a live-fetch result to the overview channel shape.

    GA4 maps sessions→clicks, pageviews→impressions since those are the
    visit-volume metrics the dashboard chart cares about. Returns None when
    the source reported no useful data.
    """
    if data.get("status") != "ok":
        return None

    if channel == "ga4":
        clicks = data.get("total_sessions", 0) or 0
        impressions = data.get("total_pageviews", 0) or 0
    else:
        clicks = data.get("total_clicks", 0) or 0
        impressions = data.get("total_impressions", 0) or 0

    if not clicks and not impressions:
        return None

    return {
        "channel": channel,
        "clicks": clicks,
        "impressions": impressions,
        "conversions": int(data.get("total_conversions", 0) or 0),
        "spend": round(float(
            data.get("total_spend", 0) or data.get("total_ad_spend", 0) or 0
        ), 2),
    }


@router.get("/overview")
async def analytics_overview(request: Request, days: int = 30, compare: int = 0):
    """
    Cross-channel analytics overview for the current tenant.

    Strategy:
    1. Call all live fetchers in parallel (same as /probe) — these always
       reflect the current state of GSC, GA4, Ads, etc.
    2. Read daily_metrics from DB for the historical daily time series.
    3. Merge: DB wins for channels it has rows for (covers the full window).
       Live data fills in any channel where the DB has zero rows — this means
       a chart point appears even if the DB write has never succeeded.
    4. If DB has no daily rows at all, synthesise today's entry from live
       totals so the chart renders something rather than a flat line.
    """
    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_ANALYTICS_OVERVIEW
        return DEMO_ANALYTICS_OVERVIEW

    days = max(1, min(int(days), 365))
    tenant_id = getattr(request.state, "tenant_id", "default")

    # ── 1. Live fetch ─────────────────────────────────────────────────
    live_channels: Dict[str, Dict[str, Any]] = {}
    try:
        config = await get_tenant_config(tenant_id)
        agent = AnalyticsAgent(tenant_config=config)

        seo_r, ads_r, reviews_r, content_r, ga4_r = await asyncio.gather(
            _safe_live("seo", lambda: agent._fetch_seo_data(date_range=days)),
            _safe_live("ads", lambda: agent._fetch_ads_data(date_range=days)),
            _safe_live("reviews", agent._fetch_reviews_data),
            _safe_live("content", agent._fetch_content_data),
            _safe_live("ga4", lambda: agent._fetch_ga4_data(date_range=days)),
        )

        for ch, raw in (
            ("seo", seo_r),
            ("google_ads", ads_r),
            ("reviews", reviews_r),
            ("content", content_r),
            ("ga4", ga4_r),
        ):
            entry = _live_to_channel(ch, raw)
            if entry:
                live_channels[ch] = entry

        logger.info(
            f"overview live fetch: {len(live_channels)}/{5} channels have data"
            f" — {list(live_channels.keys())}"
        )
    except Exception as e:
        logger.error(f"overview live fetch failed: {e}", exc_info=True)

    # ── 2. DB read for historical daily series ────────────────────────
    db_rows: List[Dict[str, Any]] = []
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
        db_rows = result.data or []
        logger.info(f"overview DB: {len(db_rows)} rows for last {days}d")
    except Exception as e:
        logger.warning(f"daily_metrics read failed: {e}")

    # ── 3. Build channels (DB + live fallback) ────────────────────────
    db_ch_map: Dict[str, Dict[str, Any]] = {}
    for r in db_rows:
        ch = r.get("channel") or "unknown"
        entry = db_ch_map.setdefault(ch, {
            "channel": ch, "clicks": 0, "impressions": 0,
            "conversions": 0, "spend": 0.0,
        })
        entry["clicks"] += r.get("total_clicks") or 0
        entry["impressions"] += r.get("total_impressions") or 0
        entry["conversions"] += int(r.get("total_conversions") or 0)
        entry["spend"] += float(r.get("total_ad_spend") or 0)

    merged: Dict[str, Dict[str, Any]] = {**db_ch_map}
    for ch, live_entry in live_channels.items():
        db_entry = merged.get(ch)
        if not db_entry or (db_entry["clicks"] == 0 and db_entry["impressions"] == 0):
            merged[ch] = live_entry

    channels = sorted(
        ({**c, "spend": round(c.get("spend", 0.0), 2)} for c in merged.values()),
        key=lambda c: c["clicks"],
        reverse=True,
    )

    # ── 4. Build daily series (DB + synthetic today if empty) ─────────
    daily_map: Dict[str, Dict[str, Any]] = {}
    for r in db_rows:
        date = r.get("date") or ""
        if not date:
            continue
        entry = daily_map.setdefault(date, {"date": date, "clicks": 0, "impressions": 0})
        entry["clicks"] += r.get("total_clicks") or 0
        entry["impressions"] += r.get("total_impressions") or 0

    if not daily_map and live_channels:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        total_clicks = sum(c["clicks"] for c in live_channels.values())
        total_impressions = sum(c["impressions"] for c in live_channels.values())
        if total_clicks or total_impressions:
            daily_map[today_str] = {
                "date": today_str,
                "clicks": total_clicks,
                "impressions": total_impressions,
            }

    daily = sorted(daily_map.values(), key=lambda d: d["date"])

    # ── 5. Totals ─────────────────────────────────────────────────────
    if db_rows:
        totals = _sum_totals(db_rows)
    elif live_channels:
        totals = {
            "clicks": sum(c["clicks"] for c in live_channels.values()),
            "impressions": sum(c["impressions"] for c in live_channels.values()),
            "conversions": sum(c.get("conversions", 0) for c in live_channels.values()),
            "spend": round(sum(c.get("spend", 0.0) for c in live_channels.values()), 2),
        }
    else:
        totals = {"clicks": 0, "impressions": 0, "conversions": 0, "spend": 0.0}

    response: Dict[str, Any] = {
        "channels": channels,
        "daily": daily,
        "totals": totals,
    }

    if compare:
        prev_end = datetime.utcnow().date() - timedelta(days=days)
        prev_start = prev_end - timedelta(days=days - 1)
        try:
            sb = get_supabase()
            prev_result = (
                sb.table("daily_metrics")
                .select("*")
                .gte("date", prev_start.isoformat())
                .lte("date", prev_end.isoformat())
                .execute()
            )
            response["previous_totals"] = _sum_totals(prev_result.data or [])
        except Exception:
            response["previous_totals"] = {
                "clicks": 0, "impressions": 0, "conversions": 0, "spend": 0.0,
            }

    return response
