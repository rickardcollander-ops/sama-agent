from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

from agents.orchestrator import orchestrator

router = APIRouter()


class GoalRequest(BaseModel):
    goal: str
    context: Optional[Dict[str, Any]] = None


@router.post("/process-goal")
async def process_goal(request: GoalRequest):
    """Process a marketing goal and generate execution plan"""
    try:
        plan = await orchestrator.process_goal(request.goal, request.context)
        return {"success": True, "plan": plan}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status():
    """Get orchestrator status"""
    return {
        "agent": "orchestrator",
        "status": "operational",
        "model": "claude-sonnet-4-20250514"
    }
