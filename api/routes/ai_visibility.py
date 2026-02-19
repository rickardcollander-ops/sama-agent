"""
AI Visibility Agent API Routes
Endpoints for monitoring and improving Successifier's presence in AI-generated answers.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from agents.ai_visibility import ai_visibility_agent

router = APIRouter()


class GapStatusUpdate(BaseModel):
    gap_id: str
    status: str  # open / in_progress / resolved


@router.get("/status")
async def get_status():
    """Agent health check and current configuration"""
    return {
        "agent": "ai_visibility",
        "status": "operational",
        "description": "Generative Engine Optimization - monitors AI assistant recommendations",
        "capabilities": [
            "visibility_check",
            "gap_analysis",
            "geo_recommendations",
        ],
    }


@router.post("/check")
async def run_visibility_check(background_tasks: BackgroundTasks):
    """
    Run a full AI visibility monitoring cycle (background).
    Queries all monitoring prompts and records results.
    Takes ~2-3 minutes to complete.
    """
    background_tasks.add_task(ai_visibility_agent.run_visibility_check)
    return {
        "message": "AI visibility check started in background",
        "note": "Queries all monitoring prompts and records mentions, ranks, and gaps",
    }


@router.post("/check/sync")
async def run_visibility_check_sync():
    """
    Run a full AI visibility monitoring cycle (synchronous).
    Use for testing; for production prefer /check (background).
    """
    try:
        result = await ai_visibility_agent.run_visibility_check()
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_visibility_summary():
    """
    Get aggregated AI visibility metrics for the last 30 days.
    Returns mention rate, average rank, top competing tools, and open gaps.
    """
    try:
        summary = await ai_visibility_agent.get_visibility_summary()
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/checks")
async def get_recent_checks(limit: int = 50):
    """Get the most recent monitoring check results."""
    try:
        checks = await ai_visibility_agent.get_recent_checks(limit=limit)
        return {"checks": checks, "count": len(checks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gaps")
async def get_open_gaps(limit: int = 20):
    """
    Get open visibility gaps - queries where competitors are recommended instead.
    Ordered by priority (high → medium → low).
    """
    try:
        gaps = await ai_visibility_agent.get_open_gaps(limit=limit)
        return {"gaps": gaps, "count": len(gaps)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gaps/update")
async def update_gap_status(body: GapStatusUpdate):
    """Update the status of a visibility gap (open / in_progress / resolved)."""
    allowed = {"open", "in_progress", "resolved"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of: {allowed}")
    try:
        result = await ai_visibility_agent.close_gap(body.gap_id, body.status)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommendations")
async def generate_geo_recommendations():
    """
    Use Claude to generate specific GEO recommendations based on recent visibility data.
    Returns 5 prioritized actions to improve AI recommendation presence.
    """
    try:
        result = await ai_visibility_agent.generate_geo_recommendations()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
