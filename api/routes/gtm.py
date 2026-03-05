"""
GTM (Go-To-Market) Strategy Agent API Routes
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

from agents.gtm import gtm_agent

router = APIRouter()


class StrategyRequest(BaseModel):
    focus: str = "full"  # full, content, outreach, ads, expansion


# ── Status ───────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """GTM agent status"""
    return {
        "agent": "gtm",
        "status": "operational",
        "capabilities": [
            "analyze_icp",
            "generate_strategy",
            "generate_signals",
            "review_performance",
            "sync_pipeline"
        ]
    }


# ── ICP Analysis ─────────────────────────────────────────────────────

@router.post("/icp/analyze")
async def analyze_icp():
    """Analyze ICP from pipeline + marketing data"""
    try:
        result = await gtm_agent.analyze_icp()
        return {"success": True, "analysis": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/icp/latest")
async def get_latest_icp():
    """Get the most recent ICP analysis"""
    try:
        icp = await gtm_agent._get_latest_icp()
        if icp:
            return {"success": True, "icp": icp}
        # Fall back to brand voice defaults
        from agents.brand_voice import BrandVoice
        return {"success": True, "icp": BrandVoice.TARGET_PERSONA, "source": "default"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Strategy ─────────────────────────────────────────────────────────

@router.post("/strategy/generate")
async def generate_strategy(request: StrategyRequest):
    """Generate GTM strategy"""
    try:
        result = await gtm_agent.generate_strategy(focus=request.focus)
        return {"success": True, "strategy": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/latest")
async def get_latest_strategy():
    """Get the most recent GTM strategy"""
    try:
        strategy = await gtm_agent._get_latest_strategy()
        if strategy:
            return {"success": True, "strategy": strategy}
        return {"success": True, "strategy": None, "message": "No strategy generated yet"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Signals ──────────────────────────────────────────────────────────

@router.post("/signals/generate")
async def generate_signals():
    """Generate cross-system signals for all agents"""
    try:
        result = await gtm_agent.generate_signals()
        return {"success": True, "signals": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Performance Review ───────────────────────────────────────────────

@router.post("/review")
async def review_performance():
    """Review GTM performance"""
    try:
        result = await gtm_agent.review_performance()
        return {"success": True, "review": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Pipeline Sync ────────────────────────────────────────────────────

@router.get("/pipeline/stats")
async def get_pipeline_stats():
    """Get pipeline statistics from Growth Hub CRM"""
    try:
        stats = await gtm_agent.fetch_pipeline_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipeline/prospects")
async def get_pipeline_prospects(status: Optional[str] = None):
    """Get prospects from Growth Hub CRM"""
    try:
        prospects = await gtm_agent.fetch_prospects(status=status)
        return {"success": True, "prospects": prospects, "count": len(prospects)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard Summary ────────────────────────────────────────────────

@router.get("/dashboard")
async def get_gtm_dashboard():
    """Get aggregated GTM dashboard data"""
    try:
        icp = await gtm_agent._get_latest_icp()
        strategy = await gtm_agent._get_latest_strategy()
        pipeline = await gtm_agent.fetch_pipeline_stats()
        marketing = await gtm_agent.fetch_marketing_metrics()

        return {
            "success": True,
            "dashboard": {
                "icp": icp,
                "strategy": strategy,
                "pipeline": pipeline,
                "marketing_summary": {
                    "top_keywords_count": len(marketing.get("top_keywords", [])),
                    "top_content_count": len(marketing.get("top_content", [])),
                    "has_daily_metrics": len(marketing.get("daily_metrics", [])) > 0
                }
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
