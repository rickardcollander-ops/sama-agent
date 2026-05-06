"""
AI Visibility API Routes
GEO (Generative Engine Optimization) monitoring endpoints
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agents.ai_visibility import AIVisibilityAgent, ai_visibility_agent
from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


class GapUpdateRequest(BaseModel):
    gap_id: str
    status: str  # open | in_progress | resolved


# ── Status ────────────────────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    return {"agent": "ai_visibility", "status": "operational"}


# ── Summary ─────────────────────────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_summary(request: Request):
    """Mention rate, avg rank, top competitors, trend"""
    tenant_id = getattr(request.state, "tenant_id", "default")
    return ai_visibility_agent.get_summary(tenant_id=tenant_id)


# ── Checks ──────────────────────────────────────────────────────────────────────────────────

@router.get("/checks")
async def get_checks(request: Request, limit: int = 50):
    """All monitoring checks for the calling tenant, newest first"""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        data = (
            sb.table("ai_visibility_checks")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("checked_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"checks": data.data or []}
    except Exception as e:
        logger.error(f"get_checks error: {e}")
        return {"checks": []}


# ── Gaps ───────────────────────────────────────────────────────────────────────────────────────

@router.get("/gaps")
async def get_gaps(request: Request):
    """Open gaps for the calling tenant, sorted by priority"""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        data = (
            sb.table("ai_visibility_gaps")
            .select("*")
            .eq("tenant_id", tenant_id)
            .neq("status", "resolved")
            .order("created_at", desc=True)
            .execute()
        )
        gaps = data.data or []
        priority_order = {"high": 0, "medium": 1, "low": 2}
        gaps.sort(key=lambda g: priority_order.get(g.get("priority", "low"), 2))
        return {"gaps": gaps}
    except Exception as e:
        logger.error(f"get_gaps error: {e}")
        return {"gaps": []}


# ── Run check ────────────────────────────────────────────────────────────────────────────────

def _finalize_run(run_id: Optional[str], status: str, summary: str = "", error: Optional[str] = None) -> None:
    """Update the agent_runs row when the monitoring thread finishes."""
    if not run_id:
        return
    try:
        sb = get_supabase()
        update = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }
        if error:
            update["error"] = error[:500]
        sb.table("agent_runs").update(update).eq("id", run_id).execute()
    except Exception:
        logger.warning(f"Could not update agent_runs {run_id}", exc_info=True)


def _run_check_thread(agent: AIVisibilityAgent, label: str, run_id: Optional[str]):
    try:
        logger.info(f"AI Visibility monitoring thread started for {label} (run_id={run_id})")
        result = asyncio.run(agent.run_monitoring(run_id_for_progress=run_id))
        logger.info(f"AI Visibility monitoring done for {label}: {result}")
        if isinstance(result, dict):
            checks = result.get("checks_run", 0)
            mention_rate = result.get("mention_rate")
            if mention_rate is not None:
                summary_text = f"{checks} checks, {round(mention_rate * 100)}% mention rate"
            else:
                summary_text = f"{checks} checks completed"
        else:
            summary_text = "GEO check completed"
        _finalize_run(run_id, "completed", summary=summary_text)
    except Exception as e:
        logger.error(f"AI Visibility monitoring thread error for {label}: {e}", exc_info=True)
        _finalize_run(run_id, "failed", error=str(e))


@router.post("/check")
async def run_check(request: Request):
    """
    Kick off a monitoring run in a background thread.
    For tenant requests, build a per-tenant agent so the run uses the
    user's saved geo_queries, brand name and competitors.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    if tenant_id and tenant_id != "default":
        config = await get_tenant_config(tenant_id)
        agent = AIVisibilityAgent(tenant_config=config)
        label = f"tenant={tenant_id}"
        if not config.geo_queries:
            logger.warning(f"Tenant {tenant_id} has no geo_queries configured; falling back to default prompts")
    else:
        agent = ai_visibility_agent
        label = "default"

    run_id: Optional[str] = None
    if tenant_id and tenant_id != "default":
        try:
            sb = get_supabase()
            insert = sb.table("agent_runs").insert({
                "tenant_id": tenant_id,
                "agent_name": "ai_visibility",
                "status": "running",
            }).execute()
            if insert.data:
                run_id = insert.data[0].get("id")
        except Exception:
            logger.warning("Could not insert agent_runs row for AI visibility check", exc_info=True)

    t = threading.Thread(target=_run_check_thread, args=(agent, label, run_id), daemon=True)
    t.start()
    logger.info(f"AI Visibility monitoring thread started: {t.name} ({label}, run_id={run_id})")
    return {
        "status": "started",
        "run_id": run_id,
        "message": "Monitoring thread started. Results appear as checks complete.",
    }


# ── Update gap ────────────────────────────────────────────────────────────────────────────────

@router.post("/gaps/update")
async def update_gap(req: GapUpdateRequest, request: Request):
    """Mark a gap as in_progress or resolved — verified against calling tenant."""
    if req.status not in ("open", "in_progress", "resolved"):
        return {"error": "Invalid status"}
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("ai_visibility_gaps").update({"status": req.status}).eq("id", req.gap_id).eq("tenant_id", tenant_id).execute()
        return {"success": True, "gap_id": req.gap_id, "status": req.status}
    except Exception as e:
        logger.error(f"update_gap error: {e}")
        return {"error": str(e)}


# ── Clear data ─────────────────────────────────────────────────────────────────────────────

@router.post("/clear")
async def clear_data(request: Request):
    """Delete all checks and gaps for the calling tenant only."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("ai_visibility_checks").delete().eq("tenant_id", tenant_id).execute()
        sb.table("ai_visibility_gaps").delete().eq("tenant_id", tenant_id).execute()
        return {"success": True, "message": f"All checks and gaps cleared for tenant {tenant_id}."}
    except Exception as e:
        logger.error(f"clear_data error: {e}")
        return {"error": str(e)}


# ── Debug: single sync test ────────────────────────────────────────────────────────────────────────────────

@router.get("/test")
async def test_single(request: Request):
    """Run ONE prompt against ONE engine synchronously and return result — for debugging"""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        from anthropic import Anthropic
        from shared.config import settings
        c = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = c.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=200,
            system="You are ChatGPT. Answer helpfully.",
            messages=[{"role": "user", "content": "What are the best customer success tools?"}],
        )
        response_text = msg.content[0].text
        mentioned = "successifier" in response_text.lower()
        sb = get_supabase()
        sb.table("ai_visibility_checks").insert({
            "tenant_id": tenant_id,
            "prompt": "TEST: What are the best customer success tools?",
            "category": "tool_recommendation",
            "ai_engine": "ChatGPT (GPT-4o)",
            "run_id": "test",
            "mentioned": mentioned,
            "rank": None,
            "competitors_mentioned": [],
            "sentiment": None,
            "ai_response_excerpt": response_text[:200],
            "full_response": response_text,
        }).execute()
        return {"ok": True, "mentioned": mentioned, "response_preview": response_text[:300], "db_insert": "success"}
    except Exception as e:
        return {"ok": False, "error": str(e), "error_type": type(e).__name__}


# ── Strategic Analysis ───────────────────────────────────────────────────────────────────────────────

@router.post("/strategic-analysis")
async def strategic_analysis(request: Request):
    """Generate a comprehensive, opinionated strategic analysis of AI visibility"""
    tenant_id = getattr(request.state, "tenant_id", "default")
    if tenant_id and tenant_id != "default":
        config = await get_tenant_config(tenant_id)
        agent = AIVisibilityAgent(tenant_config=config)
    else:
        agent = ai_visibility_agent
    result = await agent.generate_strategic_analysis()
    return result


# ── GEO Recommendations ─────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations")
async def generate_recommendations(request: Request):
    """Generate Claude-powered GEO recommendations from open gaps"""
    tenant_id = getattr(request.state, "tenant_id", "default")
    if tenant_id and tenant_id != "default":
        config = await get_tenant_config(tenant_id)
        agent = AIVisibilityAgent(tenant_config=config)
    else:
        agent = ai_visibility_agent
    recommendations = await agent.generate_geo_recommendations()
    return {"recommendations": recommendations}
