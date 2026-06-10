"""
AI Visibility API Routes
GEO (Generative Engine Optimization) monitoring endpoints
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agents.ai_visibility import AIVisibilityAgent, ai_visibility_agent
from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


# Each tracked query fans out across 5 AI engines, so capping manual runs at
# one per week keeps token spend tied to the weekly cron cycle. The dashboard
# countdown reads `next_available_at` from /lock-status to render the timer.
AI_VISIBILITY_LOCK_DAYS = 7


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Supabase returns "2026-05-10T08:23:00.123+00:00" or "...Z".
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def _latest_completed_run(tenant_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent completed ai_visibility agent_runs row, or None."""
    if not tenant_id or tenant_id == "default":
        return None
    try:
        sb = get_supabase()
        result = (
            sb.table("agent_runs")
            .select("id,status,completed_at")
            .eq("tenant_id", tenant_id)
            .eq("agent_name", "ai_visibility")
            .eq("status", "completed")
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.debug(f"_latest_completed_run lookup failed: {e}")
        return None


def _compute_lock(tenant_id: str) -> Dict[str, Any]:
    """Compute lock status for the manual /check endpoint.

    Returns a dict with `locked`, `last_completed_at`, `next_available_at`
    (all ISO strings or None). A tenant with no completed runs yet is never
    locked — the first run can always go through.
    """
    last = _latest_completed_run(tenant_id)
    last_completed_at = (last or {}).get("completed_at")
    last_dt = _parse_iso(last_completed_at)
    if not last_dt:
        return {
            "locked": False,
            "last_completed_at": None,
            "next_available_at": None,
        }
    next_dt = last_dt + timedelta(days=AI_VISIBILITY_LOCK_DAYS)
    return {
        "locked": datetime.now(timezone.utc) < next_dt,
        "last_completed_at": last_completed_at,
        "next_available_at": next_dt.isoformat(),
    }


def _read_geo_queries_updated_at(tenant_id: str) -> Optional[str]:
    """Return the timestamp the tenant last edited their saved queries.

    The dashboard writes this into user_settings.settings whenever the user
    adds or removes a query. Used by the dashboard to render a "review your
    tracked queries" reminder once the saved set has gone untouched for too
    long. None when missing or unparsable — the dashboard treats that as
    "never edited" and shows the reminder.
    """
    if not tenant_id or tenant_id == "default":
        return None
    try:
        sb = get_supabase()
        result = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        settings = (result.data or {}).get("settings") or {}
        ts = settings.get("geo_queries_updated_at")
        return ts if isinstance(ts, str) else None
    except Exception as e:
        logger.debug(f"_read_geo_queries_updated_at failed: {e}")
        return None


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


# ── Lock status ─────────────────────────────────────────────────────────────────────────────

@router.get("/lock-status")
async def get_lock_status(request: Request):
    """Return the manual-check cooldown state for the dashboard countdown.

    `locked`: whether a manual run would be refused right now.
    `last_completed_at` / `next_available_at`: ISO timestamps for the timer.
    `geo_queries_updated_at`: when the tenant last edited the saved set;
    used to surface the "review your tracked queries" reminder banner.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    lock = _compute_lock(tenant_id)
    return {
        **lock,
        "lock_days": AI_VISIBILITY_LOCK_DAYS,
        "geo_queries_updated_at": _read_geo_queries_updated_at(tenant_id),
    }


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


def _run_check_thread(
    agent: AIVisibilityAgent,
    label: str,
    run_id: Optional[str],
    tenant_id: Optional[str] = None,
):
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

        # Auto-feed: translate freshly-created ai_visibility_gaps into
        # plan items so the GEO findings show up alongside SEO/content gaps
        # on the unified Plan list.
        if tenant_id:
            try:
                from agents.ai_visibility import _gaps_to_content_actions
                from api.routes.content_plan import upsert_analysis_gap_items
                sb = get_supabase()
                gaps_q = sb.table("ai_visibility_gaps").select("*").eq("status", "open")
                if tenant_id != "default":
                    gaps_q = gaps_q.eq("tenant_id", tenant_id)
                gaps = gaps_q.order("created_at", desc=True).limit(50).execute().data or []
                actions = _gaps_to_content_actions(gaps)
                if actions:
                    inserted = upsert_analysis_gap_items(
                        tenant_id, actions, cycle_id=run_id, source="ai_visibility_gap"
                    )
                    logger.info(f"AI visibility → plan: {inserted} items added for {tenant_id}")
            except Exception as e:
                logger.debug(f"AI visibility → plan auto-feed skipped: {e}")

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

    Tenants without any saved ``geo_queries`` get a 400 instead of a silent
    auto-generated run — the previous behaviour ran English fallback prompts
    that no one had configured, which broke the "only what you put in AI
    Assistant gets measured" contract the dashboard surfaces to users.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    if tenant_id and tenant_id != "default":
        config = await get_tenant_config(tenant_id)
        if not config.geo_queries:
            return {
                "status": "skipped",
                "error": "no_saved_queries",
                "message": (
                    "No queries configured. Add the prompts you want measured "
                    "under AI Assistant before running a check."
                ),
            }
        # 7-day cooldown: refuse manual reruns within the weekly cycle so
        # token spend stays bounded. The scheduled cron triggers in the same
        # window, so users see a countdown instead of a re-run button.
        lock = _compute_lock(tenant_id)
        if lock["locked"]:
            return {
                "status": "locked",
                "error": "cooldown_active",
                "message": (
                    "AI Assistant check ran less than "
                    f"{AI_VISIBILITY_LOCK_DAYS} days ago. The next run is "
                    "scheduled automatically — see the countdown on the AI "
                    "Assistants page."
                ),
                "last_completed_at": lock["last_completed_at"],
                "next_available_at": lock["next_available_at"],
            }
        agent = AIVisibilityAgent(tenant_config=config)
        label = f"tenant={tenant_id}"
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

    t = threading.Thread(
        target=_run_check_thread,
        args=(agent, label, run_id, tenant_id),
        daemon=True,
    )
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
        from shared.llm import call_claude
        c = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = await call_claude(
            client=c,
            model=settings.CLAUDE_MODEL,
            max_tokens=200,
            system="You are ChatGPT. Answer helpfully.",
            messages=[{"role": "user", "content": "What are the best customer success tools?"}],
            tenant_id=tenant_id,
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
