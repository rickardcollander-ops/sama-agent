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
    """Get daily metrics from Supabase"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("daily_metrics").select("*").order("date", desc=True).limit(days).execute()
        return {"metrics": result.data or []}
    except Exception as e:
        return {"metrics": [], "error": str(e)}


@router.get("/status")
async def get_status():
    """Get Analytics agent status"""
    return {
        "agent": "analytics",
        "status": "operational",
        "channels": list(analytics_agent.CHANNEL_METRICS.keys()),
        "attribution_models": list(analytics_agent.ATTRIBUTION_MODELS.keys()),
        "report_templates": list(analytics_agent.REPORT_TEMPLATES.keys())
    }


@router.get("/report/weekly")
async def get_weekly_report(date_range: int = 7):
    """Generate weekly marketing report"""
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
