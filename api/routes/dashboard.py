"""
Dashboard API — Cross-agent intelligence for the SAMA command center.
Aggregates data from all agents and generates smart recommendations.
"""

from fastapi import APIRouter, Request
from typing import Dict, Any, List, Optional
import logging
import asyncio
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/status")
async def get_dashboard_status(request: Request):
    """
    Aggregated status from all agents + Supabase.
    Returns health, last activity, and key counts — scoped to the calling tenant.
    """
    from shared.database import get_supabase

    tenant_id = getattr(request.state, "tenant_id", "default")

    status: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "agents": {},
        "counts": {},
    }

    try:
        sb = get_supabase()

        kw_res, content_res, audit_res, alert_res, log_res = await asyncio.gather(
            asyncio.to_thread(lambda: _safe_count(sb, "seo_keywords", tenant_id)),
            asyncio.to_thread(lambda: _safe_count(sb, "content_pieces", tenant_id)),
            asyncio.to_thread(lambda: _safe_count(sb, "seo_audits", tenant_id)),
            asyncio.to_thread(lambda: _safe_count(sb, "alerts", tenant_id)),
            asyncio.to_thread(lambda: _safe_recent(sb, "agent_logs", 5, tenant_id)),
        )

        status["counts"] = {
            "keywords": kw_res,
            "content_pieces": content_res,
            "seo_audits": audit_res,
            "alerts": alert_res,
        }
        status["recent_activity"] = log_res

    except Exception as e:
        logger.warning(f"Dashboard status DB error: {e}")
        status["db_error"] = str(e)

    for agent_name in ["seo", "content", "ads", "social", "reviews", "analytics", "ai_visibility"]:
        status["agents"][agent_name] = "operational"

    try:
        from shared import scheduler as job_scheduler
        job_history = job_scheduler.get_job_history()
        next_runs = {}
        if job_scheduler.scheduler.running:
            for job in job_scheduler.scheduler.get_jobs():
                next_run = job.next_run_time
                next_runs[job.id] = next_run.isoformat() if next_run else None
        status["scheduler"] = {
            "running": job_scheduler.scheduler.running,
            "jobs": {
                name: {
                    **info,
                    "next_run": next_runs.get(name),
                }
                for name, info in job_history.items()
            },
        }
    except Exception as e:
        logger.warning(f"Scheduler status error: {e}")
        status["scheduler"] = {"running": False, "error": str(e)}

    try:
        sb2 = get_supabase()
        actions_res = (
            sb2.table("agent_actions")
            .select("agent_name")
            .eq("tenant_id", tenant_id)
            .eq("status", "pending")
            .execute()
        )
        pending_actions = actions_res.data or []
        from collections import Counter
        agent_pending = Counter(a["agent_name"] for a in pending_actions)
        status["pending_actions"] = dict(agent_pending)
        status["counts"]["pending_actions"] = len(pending_actions)
    except Exception:
        status["pending_actions"] = {}
        status["counts"]["pending_actions"] = 0

    try:
        sb3 = get_supabase()
        reviews_res = sb3.table("reviews").select("rating").eq("tenant_id", tenant_id).execute()
        reviews = reviews_res.data or []
        if reviews:
            ratings = [r["rating"] for r in reviews if r.get("rating")]
            status["counts"]["reviews"] = len(reviews)
            status["counts"]["avg_rating"] = round(sum(ratings) / len(ratings), 1) if ratings else 0
        else:
            status["counts"]["reviews"] = 0
            status["counts"]["avg_rating"] = 0
    except Exception:
        status["counts"]["reviews"] = 0
        status["counts"]["avg_rating"] = 0

    return status


@router.get("/pending-actions")
async def get_all_pending_actions(request: Request):
    """Return all pending agent actions for the calling tenant."""
    from shared.actions_db import get_pending_actions
    tenant_id = getattr(request.state, "tenant_id", "default")
    actions = await get_pending_actions(tenant_id=tenant_id)
    return {"success": True, "total": len(actions), "actions": actions}


@router.post("/actions/{action_id}/execute")
async def execute_action_by_id(action_id: str, request: Request):
    """
    Universal action execution endpoint.
    Fetches the action from DB, verifies tenant ownership,
    routes to the correct agent's execute endpoint,
    and marks it as completed regardless of result.
    """
    from shared.database import get_supabase
    import httpx
    from shared.config import settings

    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    result = (
        sb.table("agent_actions")
        .select("*")
        .eq("id", action_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not result.data:
        return {"success": False, "error": "Action not found"}

    action = result.data[0]
    agent = action.get("agent_name", "")
    action_type = action.get("action_type", "")

    agent_routes = {
        "seo": "/api/seo/execute",
        "content": "/api/content/execute",
        "ads": "/api/ads/execute",
        "social": "/api/social/execute",
        "reviews": "/api/reviews/execute",
    }

    route = agent_routes.get(agent)
    execute_result = None

    if route:
        try:
            payload = {
                "id": action_id,
                "action_type": action_type,
                "type": action_type,
                "keyword": action.get("keyword", ""),
                "title": action.get("title", ""),
                "description": action.get("description", ""),
                "competitor": action.get("competitor", ""),
                "content_id": action.get("content_id", ""),
            }
            api_url = settings.SAMA_API_URL
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{api_url}{route}", json=payload)
                execute_result = resp.json()
        except Exception as e:
            logger.warning(f"Agent execute failed for {agent}/{action_type}: {e}")
            execute_result = {"error": str(e)}

    try:
        success = execute_result.get("success", False) if execute_result else False
        sb.table("agent_actions").update({
            "status": "completed" if success else "failed",
            "executed_at": datetime.utcnow().isoformat(),
            "execution_result": execute_result,
        }).eq("id", action_id).eq("tenant_id", tenant_id).execute()
    except Exception as e:
        logger.error(f"Failed to update action status: {e}")

    return {
        "success": True,
        "action_id": action_id,
        "agent": agent,
        "execute_result": execute_result,
    }


@router.delete("/actions/{action_id}")
async def dismiss_action(action_id: str, request: Request):
    """
    Universal action dismiss endpoint.
    Verifies tenant ownership before dismissing.
    """
    from shared.database import get_supabase

    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        sb.table("agent_actions").update({
            "status": "dismissed",
            "executed_at": datetime.utcnow().isoformat(),
        }).eq("id", action_id).eq("tenant_id", tenant_id).execute()
        return {"success": True, "message": f"Action {action_id} dismissed"}
    except Exception as e:
        logger.error(f"Failed to dismiss action: {e}")
        return {"success": False, "error": str(e)}


@router.get("/recommendations")
async def get_smart_recommendations(request: Request):
    """
    Cross-agent smart recommendations, scoped to the calling tenant.
    Analyzes current state and suggests highest-impact actions.
    """
    from shared.database import get_supabase
    import asyncio

    tenant_id = getattr(request.state, "tenant_id", "default")
    recommendations: List[Dict[str, Any]] = []

    try:
        sb = get_supabase()

        kw_data, alert_data, audit_data, content_data = await asyncio.gather(
            asyncio.to_thread(lambda: _safe_query(sb, "seo_keywords", "keyword,current_position,current_ctr,current_impressions,last_checked_at", 100, tenant_id)),
            asyncio.to_thread(lambda: _safe_query(sb, "alerts", "id,alert_type,severity,status,created_at", 50, tenant_id)),
            asyncio.to_thread(lambda: _safe_query(sb, "seo_audits", "id,audit_date,critical_issues,high_issues", 3, tenant_id)),
            asyncio.to_thread(lambda: _safe_query(sb, "content_pieces", "id,title,status,impressions_30d,clicks_30d,created_at", 50, tenant_id)),
        )

        # 1. Keywords losing position — high impact
        if kw_data:
            declining = [k for k in kw_data if (k.get("current_position") or 100) > 10 and (k.get("current_impressions") or 0) > 50]
            if declining:
                top = sorted(declining, key=lambda k: k.get("current_impressions", 0), reverse=True)[:3]
                kw_names = ", ".join(k["keyword"] for k in top)
                recommendations.append({
                    "id": "kw-position-drop",
                    "title": "Keywords outside top 10 with high impressions",
                    "description": f"{len(declining)} keywords with high impressions but ranking below #10. Focus on: {kw_names}",
                    "priority": "high",
                    "agent": "seo",
                    "action": "Run SEO audit and optimize these pages",
                    "impact": "high",
                    "effort": "medium",
                })

            low_ctr = [k for k in kw_data if (k.get("current_position") or 100) <= 5 and (k.get("current_ctr") or 0) < 3.0 and (k.get("current_impressions") or 0) > 20]
            if low_ctr:
                recommendations.append({
                    "id": "ctr-optimization",
                    "title": "Low CTR on top-ranking keywords",
                    "description": f"{len(low_ctr)} keywords rank in top 5 but have CTR under 3%. Improve title tags and meta descriptions.",
                    "priority": "high",
                    "agent": "seo",
                    "action": "Optimize meta titles and descriptions",
                    "impact": "high",
                    "effort": "low",
                })

            stale = [k for k in kw_data if not k.get("last_checked_at")]
            if len(stale) > len(kw_data) * 0.3:
                recommendations.append({
                    "id": "stale-keywords",
                    "title": "Keyword data needs refresh",
                    "description": f"{len(stale)} of {len(kw_data)} keywords have never been checked. Run keyword tracking.",
                    "priority": "medium",
                    "agent": "seo",
                    "action": "Trigger keyword tracking sync",
                    "impact": "medium",
                    "effort": "low",
                })

        # 2. Pending alerts
        if alert_data:
            pending = [a for a in alert_data if a.get("status") == "pending"]
            if pending:
                high_sev = [a for a in pending if a.get("severity") == "high"]
                recommendations.append({
                    "id": "pending-alerts",
                    "title": f"{len(pending)} pending alerts need review",
                    "description": f"{len(high_sev)} are high severity. Review and act on pending alerts.",
                    "priority": "high" if high_sev else "medium",
                    "agent": "system",
                    "action": "Go to Approvals page",
                    "impact": "high" if high_sev else "medium",
                    "effort": "low",
                })

        # 3. SEO audit issues
        if audit_data and len(audit_data) > 0:
            latest = audit_data[0]
            critical = latest.get("critical_issues") or []
            high = latest.get("high_issues") or []
            issue_count = len(critical) + len(high)
            if issue_count > 0:
                recommendations.append({
                    "id": "seo-audit-issues",
                    "title": f"{issue_count} critical/high SEO issues found",
                    "description": f"Latest audit found {len(critical)} critical and {len(high)} high-priority issues.",
                    "priority": "high",
                    "agent": "seo",
                    "action": "Review SEO audit and fix issues",
                    "impact": "high",
                    "effort": "medium",
                })

        # 4. Content gaps
        if content_data:
            drafts = [c for c in content_data if c.get("status") == "draft"]
            if drafts:
                recommendations.append({
                    "id": "unpublished-content",
                    "title": f"{len(drafts)} draft articles ready for review",
                    "description": "Unpublished content is losing potential traffic. Review and publish.",
                    "priority": "medium",
                    "agent": "content",
                    "action": "Review draft content for publishing",
                    "impact": "medium",
                    "effort": "low",
                })

            low_perf = [c for c in content_data if (c.get("impressions_30d") or 0) < 10 and c.get("status") == "published"]
            if low_perf:
                recommendations.append({
                    "id": "low-traffic-content",
                    "title": f"{len(low_perf)} published articles getting <10 impressions",
                    "description": "Consider refreshing or consolidating underperforming content.",
                    "priority": "low",
                    "agent": "content",
                    "action": "Run content analytics to identify refresh targets",
                    "impact": "medium",
                    "effort": "medium",
                })

        # 5. General recommendations
        if not kw_data:
            recommendations.append({
                "id": "setup-keywords",
                "title": "Set up keyword tracking",
                "description": "No keywords are being tracked. Add target keywords to start monitoring rankings.",
                "priority": "high",
                "agent": "seo",
                "action": "Add keywords via SEO page",
                "impact": "high",
                "effort": "low",
            })

    except Exception as e:
        logger.warning(f"Recommendation generation error: {e}")
        recommendations.append({
            "id": "db-error",
            "title": "Could not fetch data for recommendations",
            "description": f"Database error: {str(e)[:100]}. Check backend connectivity.",
            "priority": "medium",
            "agent": "system",
            "action": "Check backend health",
            "impact": "high",
            "effort": "low",
        })

    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 3))

    return {
        "recommendations": recommendations,
        "total": len(recommendations),
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_count(sb, table: str, tenant_id: Optional[str] = None) -> int:
    try:
        q = sb.table(table).select("id", count="exact").limit(0)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        r = q.execute()
        return r.count or 0
    except Exception:
        return 0


def _safe_recent(sb, table: str, limit: int, tenant_id: Optional[str] = None) -> list:
    try:
        q = sb.table(table).select("*")
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        r = q.order("created_at", desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        return []


def _safe_query(sb, table: str, columns: str, limit: int, tenant_id: Optional[str] = None) -> list:
    try:
        q = sb.table(table).select(columns)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        r = q.limit(limit).execute()
        return r.data or []
    except Exception:
        return []
