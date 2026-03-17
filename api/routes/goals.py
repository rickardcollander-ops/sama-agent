"""
Goals API — CRUD for agent goals and progress tracking
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from shared.goals import goal_tracker

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateGoalRequest(BaseModel):
    goal_text: str
    target_metric: str
    target_value: float
    baseline_value: float
    deadline: str  # ISO date string
    owner_agent: str


class UpdateProgressRequest(BaseModel):
    current_value: float


@router.get("/goals")
async def list_goals(agent: Optional[str] = None):
    """List all active goals, optionally filtered by agent."""
    try:
        goals = await goal_tracker.get_active_goals(agent)
        enriched = []
        for g in goals:
            try:
                status = await goal_tracker.check_goal_status(g)
                enriched.append({**g, "progress_status": status})
            except Exception:
                enriched.append({**g, "progress_status": "unknown"})
        return {"goals": enriched, "total": len(enriched)}
    except Exception as e:
        logger.warning(f"[goals] list_goals failed: {e}")
        return {"goals": [], "total": 0, "error": str(e)}


@router.post("/goals")
async def create_goal(req: CreateGoalRequest):
    """Create a new goal."""
    try:
        goal = await goal_tracker.create_goal(
            goal_text=req.goal_text,
            target_metric=req.target_metric,
            target_value=req.target_value,
            baseline_value=req.baseline_value,
            deadline=req.deadline,
            owner_agent=req.owner_agent,
        )
        if not goal:
            raise HTTPException(status_code=500, detail="Failed to create goal — check that agent_goals table exists in Supabase")
        return {"success": True, "goal": goal}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[goals] create_goal failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create goal: {e}")


@router.patch("/goals/{goal_id}/progress")
async def update_progress(goal_id: str, req: UpdateProgressRequest):
    """Update current value for a goal."""
    await goal_tracker.update_progress(goal_id, req.current_value)
    return {"success": True, "message": "Progress updated"}


@router.get("/goals/prompt-context")
async def get_goal_prompt_context(agent: Optional[str] = None):
    """Get formatted goal context for OODA prompt injection."""
    context = await goal_tracker.get_prompt_context(agent)
    return {"context": context}
