from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from agents.analytics import analytics_agent

router = APIRouter()


class AttributionRequest(BaseModel):
    conversions: List[Dict[str, Any]]
    model: str = "linear"


class ROIRequest(BaseModel):
    channel: str
    date_range: int = 30


class TrendRequest(BaseModel):
    metric: str
    channel: str
    lookback_days: int = 90


class InsightsRequest(BaseModel):
    data: Dict[str, Any]


@router.get("/metrics")
async def get_metrics(days: int = 30):
    """Get daily metrics from Supabase (historical, from daily_metrics table)"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("daily_metrics").select("*").order("date", desc=True).limit(days).execute()
        rows = result.data or []
        # Ensure numeric fields are never null (prevents frontend toLocaleString crashes)
        numeric_keys = ("clicks", "impressions", "sessions", "pageviews", "conversions", "bounce_rate", "avg_position")
        for row in rows:
            for key in numeric_keys:
                if key in row and row[key] is None:
                    row[key] = 0
        return {"metrics": rows}
    except Exception as e:
        return {"metrics": [], "error": str(e)}


@router.get("/metrics/live")
async def get_live_metrics():
    """
    Fetch live metrics directly from all configured agents (SEO, Ads,
    Reviews, Content) without relying on the daily_metrics table.

    Returns real-time data aggregated from each agent's API integrations.
    """
    try:
        data = await analytics_agent.get_live_metrics()
        return {"success": True, **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metrics/collect")
async def collect_daily_metrics():
    """
    Trigger daily metrics collection from all agents and upsert into the
    daily_metrics table.  Can be called by a cron job, automation endpoint,
    or manually.
    """
    try:
        result = await analytics_agent.collect_daily_metrics()
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status():
    """Get Analytics agent status"""
    from shared.google_auth import is_gsc_configured, is_ads_configured, is_ga4_configured
    from shared.config import settings
    return {
        "agent": "analytics",
        "status": "operational",
        "channels": list(analytics_agent.CHANNEL_METRICS.keys()),
        "attribution_models": list(analytics_agent.ATTRIBUTION_MODELS.keys()),
        "report_templates": list(analytics_agent.REPORT_TEMPLATES.keys()),
        "integrations": {
            "gsc_configured": is_gsc_configured(),
            "ads_configured": is_ads_configured(),
            "ga4_configured": is_ga4_configured(),
            "ga4_property_id": settings.GA4_PROPERTY_ID[:10] + "..." if len(settings.GA4_PROPERTY_ID) > 10 else ("set" if settings.GA4_PROPERTY_ID else "missing"),
            "supabase_configured": bool(settings.SUPABASE_URL and settings.SUPABASE_KEY),
        },
    }


@router.get("/debug")
async def debug_channels():
    """
    Fetch each channel individually and report status/errors.
    Useful for diagnosing which integrations are working.
    """
    import asyncio
    results = {}

    async def _test(name, coro):
        try:
            data = await coro
            results[name] = {"status": data.get("status", "ok"), "keys": list(data.keys())}
            if data.get("error"):
                results[name]["error"] = str(data["error"])[:200]
            # Include some sample values
            for k in ("total_clicks", "total_sessions", "total_impressions", "total_pageviews", "bounce_rate", "total_reviews"):
                if k in data and data[k]:
                    results[name][k] = data[k]
        except Exception as e:
            results[name] = {"status": "exception", "error": str(e)[:200]}

    await asyncio.gather(
        _test("seo", analytics_agent._fetch_seo_data()),
        _test("ads", analytics_agent._fetch_ads_data()),
        _test("reviews", analytics_agent._fetch_reviews_data()),
        _test("content", analytics_agent._fetch_content_data()),
        _test("ga4", analytics_agent._fetch_ga4_data()),
    )

    return {"channels": results}


@router.get("/report/weekly")
async def get_weekly_report(date_range: int = 7):
    """Generate weekly marketing report with real data from all agents"""
    try:
        report = await analytics_agent.generate_weekly_report(date_range=date_range)
        return {"success": True, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/attribution")
async def calculate_attribution(request: AttributionRequest):
    """Calculate attribution across channels"""
    try:
        attribution = await analytics_agent.calculate_attribution(
            conversions=request.conversions,
            model=request.model
        )
        return {"success": True, "attribution": attribution}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/roi")
async def calculate_roi(request: ROIRequest):
    """Calculate ROI for a channel"""
    try:
        roi = await analytics_agent.calculate_roi(
            channel=request.channel,
            date_range=request.date_range
        )
        return {"success": True, "roi": roi}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trends")
async def identify_trends(request: TrendRequest):
    """Identify trends in metrics"""
    try:
        trend = await analytics_agent.identify_trends(
            metric=request.metric,
            channel=request.channel,
            lookback_days=request.lookback_days
        )
        return {"success": True, "trend": trend}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/insights")
async def generate_insights(request: InsightsRequest):
    """Generate AI-powered insights"""
    try:
        insights = await analytics_agent.generate_insights(data=request.data)
        return {"success": True, "insights": insights}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/{dashboard_type}")
async def get_dashboard(dashboard_type: str):
    """Get dashboard configuration"""
    try:
        dashboard = await analytics_agent.create_dashboard(dashboard_type=dashboard_type)
        return {"success": True, "dashboard": dashboard}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/channels")
async def get_channels():
    """Get all channel metrics"""
    return {
        "channels": analytics_agent.CHANNEL_METRICS
    }


@router.get("/attribution-models")
async def get_attribution_models():
    """Get available attribution models"""
    return {
        "models": analytics_agent.ATTRIBUTION_MODELS
    }
