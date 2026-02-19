"""
AI Visibility API Routes
GEO (Generative Engine Optimization) monitoring endpoints
"""

from fastapi import APIRouter
from pydantic import BaseModel
import logging
import threading

from agents.ai_visibility import ai_visibility_agent
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class GapUpdateRequest(BaseModel):
    gap_id: str
    status: str  # open | in_progress | resolved


# ── Status ─────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    return {"agent": "ai_visibility", "status": "operational"}


# ── Summary ────────────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_summary():
    """Mention rate, avg rank, top competitors, trend"""
    return ai_visibility_agent.get_summary()


# ── Checks ─────────────────────────────────────────────────────────────────────

@router.get("/checks")
async def get_checks(limit: int = 50):
    """All monitoring checks, newest first"""
    try:
        sb = get_supabase()
        data = (
            sb.table("ai_visibility_checks")
            .select("*")
            .order("checked_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"checks": data.data or []}
    except Exception as e:
        logger.error(f"get_checks error: {e}")
        return {"checks": []}


# ── Gaps ───────────────────────────────────────────────────────────────────────

@router.get("/gaps")
async def get_gaps():
    """Open gaps sorted by priority"""
    try:
        sb = get_supabase()
        # Fetch all gaps (open + in_progress shown, resolved hidden)
        data = (
            sb.table("ai_visibility_gaps")
            .select("*")
            .neq("status", "resolved")
            .order("created_at", desc=True)
            .execute()
        )
        gaps = data.data or []
        # Sort: high → medium → low
        priority_order = {"high": 0, "medium": 1, "low": 2}
        gaps.sort(key=lambda g: priority_order.get(g.get("priority", "low"), 2))
        return {"gaps": gaps}
    except Exception as e:
        logger.error(f"get_gaps error: {e}")
        return {"gaps": []}


# ── Run check ──────────────────────────────────────────────────────────────────

def _run_check_thread():
    try:
        logger.info("AI Visibility monitoring thread started")
        result = ai_visibility_agent.run_monitoring()
        logger.info(f"AI Visibility monitoring done: {result}")
    except Exception as e:
        logger.error(f"AI Visibility monitoring thread error: {e}", exc_info=True)


@router.post("/check")
async def run_check():
    """Kick off a monitoring run in a background thread (~13 min for 5 engines × 16 prompts)"""
    t = threading.Thread(target=_run_check_thread, daemon=True)
    t.start()
    logger.info(f"AI Visibility monitoring thread started: {t.name}")
    return {"status": "started", "message": "Monitoring thread started. Results appear as checks complete."}


# ── Update gap ─────────────────────────────────────────────────────────────────

@router.post("/gaps/update")
async def update_gap(req: GapUpdateRequest):
    """Mark a gap as in_progress or resolved"""
    if req.status not in ("open", "in_progress", "resolved"):
        return {"error": "Invalid status"}
    try:
        sb = get_supabase()
        sb.table("ai_visibility_gaps").update({"status": req.status}).eq("id", req.gap_id).execute()
        return {"success": True, "gap_id": req.gap_id, "status": req.status}
    except Exception as e:
        logger.error(f"update_gap error: {e}")
        return {"error": str(e)}


# ── Clear data ─────────────────────────────────────────────────────────────────

@router.post("/clear")
async def clear_data():
    """Delete all checks and gaps — use when old/corrupt data needs to be removed"""
    try:
        sb = get_supabase()
        sb.table("ai_visibility_checks").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        sb.table("ai_visibility_gaps").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        return {"success": True, "message": "All checks and gaps cleared."}
    except Exception as e:
        logger.error(f"clear_data error: {e}")
        return {"error": str(e)}


# ── GEO Recommendations ────────────────────────────────────────────────────────

@router.post("/recommendations")
async def generate_recommendations():
    """Generate Claude-powered GEO recommendations from open gaps"""
    recommendations = ai_visibility_agent.generate_geo_recommendations()
    return {"recommendations": recommendations}
