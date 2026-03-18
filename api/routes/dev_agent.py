"""
Dev Agent API — system health checks, diagnostics, and FORGE operations
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import logging

from agents.dev_agent import dev_agent
from shared.database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Health Checks ──────────────────────────────────────────────────────────


@router.get("/health-check")
async def run_health_check():
    """Run a full system health check and return the report."""
    report = await dev_agent.run_full_health_check()
    await dev_agent.save_report(report)
    return report


@router.get("/health-check/latest")
async def get_latest_report():
    """Get the most recent health check report (from memory or DB)."""
    report = await dev_agent.get_report_from_db()
    if report:
        return report
    return {"message": "No health check has been run yet. Trigger one via POST /api/dev-agent/health-check."}


@router.get("/health-check/endpoints")
async def test_endpoints_only():
    """Test only API endpoints (faster than full check)."""
    return await dev_agent._test_endpoints()


@router.get("/health-check/database")
async def test_database_only():
    """Test only database tables."""
    return await dev_agent._test_database()


@router.get("/health-check/scheduler")
async def test_scheduler_only():
    """Test only scheduler status."""
    return dev_agent._test_scheduler()


# ── OODA Cycle Triggers ───────────────────────────────────────────────────


OODA_AGENTS = {
    "seo": "api.routes.seo_analyze_ooda",
    "content": "api.routes.content_analyze_ooda",
    "ads": "api.routes.ads_analyze_ooda",
    "social": "api.routes.social_analyze_ooda",
    "reviews": "api.routes.reviews_analyze_ooda",
}


@router.post("/trigger-ooda/{agent_name}")
async def trigger_ooda_cycle(agent_name: str, background_tasks: BackgroundTasks):
    """Trigger an OODA cycle for a specific agent. Used by FORGE to kick agents into action."""
    if agent_name not in OODA_AGENTS:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent_name}. Available: {list(OODA_AGENTS.keys())}")

    module_path = OODA_AGENTS[agent_name]
    # Actual function names: run_seo_analysis_with_ooda, run_content_analysis_with_ooda, etc.
    func_name = f"run_{agent_name}_analysis_with_ooda"

    try:
        import importlib
        mod = importlib.import_module(module_path)
        ooda_func = getattr(mod, func_name, None)
        if not ooda_func:
            raise HTTPException(status_code=500, detail=f"Could not find {func_name} in {module_path}")

        background_tasks.add_task(ooda_func)
        logger.info(f"[dev-agent] Triggered OODA cycle for {agent_name}")
        return {"success": True, "agent": agent_name, "message": f"OODA cycle started for {agent_name}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[dev-agent] Failed to trigger OODA for {agent_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger-ooda-all")
async def trigger_all_ooda_cycles(background_tasks: BackgroundTasks):
    """Trigger OODA cycles for ALL agents. Full system analysis."""
    import importlib
    results = {}
    for agent_name, module_path in OODA_AGENTS.items():
        func_name = f"run_{agent_name}_analysis_with_ooda"
        try:
            mod = importlib.import_module(module_path)
            ooda_func = getattr(mod, func_name, None)
            if ooda_func:
                background_tasks.add_task(ooda_func)
                results[agent_name] = "started"
            else:
                results[agent_name] = "no_function_found"
        except Exception as e:
            results[agent_name] = f"error: {str(e)[:100]}"

    logger.info(f"[dev-agent] Triggered OODA for all agents: {results}")
    return {"success": True, "agents": results}


# ── Action Management ─────────────────────────────────────────────────────


@router.post("/actions/{action_id}/retry")
async def retry_action(action_id: str):
    """Reset a failed/stuck action back to pending so it can be retried."""
    try:
        sb = get_supabase()
        result = sb.table("agent_actions") \
            .update({"status": "pending", "error_message": None, "executed_at": None}) \
            .eq("action_id", action_id) \
            .execute()
        if not result.data:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
        logger.info(f"[dev-agent] Retried action {action_id}")
        return {"success": True, "action_id": action_id, "new_status": "pending"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BulkRetryRequest(BaseModel):
    agent_name: Optional[str] = None
    status_filter: str = "failed"


@router.post("/actions/retry-bulk")
async def retry_bulk_actions(request: BulkRetryRequest):
    """Retry all failed (or stuck) actions, optionally filtered by agent."""
    try:
        sb = get_supabase()
        query = sb.table("agent_actions") \
            .select("action_id,agent_name,title,status") \
            .eq("status", request.status_filter)
        if request.agent_name:
            query = query.eq("agent_name", request.agent_name)
        result = query.limit(100).execute()

        if not result.data:
            return {"success": True, "retried": 0, "message": f"No {request.status_filter} actions found"}

        retried = 0
        for action in result.data:
            try:
                sb.table("agent_actions") \
                    .update({"status": "pending", "error_message": None, "executed_at": None}) \
                    .eq("action_id", action["action_id"]) \
                    .execute()
                retried += 1
            except Exception:
                pass

        logger.info(f"[dev-agent] Bulk retry: {retried} actions reset to pending")
        return {"success": True, "retried": retried, "total_found": len(result.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actions/stuck")
async def get_stuck_actions():
    """Get actions that are stuck in pending for more than 24h or failed."""
    from datetime import datetime, timezone, timedelta
    try:
        sb = get_supabase()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        # Failed actions
        failed = sb.table("agent_actions") \
            .select("action_id,agent_name,title,status,priority,error_message,created_at") \
            .eq("status", "failed") \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()

        # Stale pending (older than 24h)
        stale = sb.table("agent_actions") \
            .select("action_id,agent_name,title,status,priority,created_at") \
            .eq("status", "pending") \
            .lte("created_at", cutoff) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()

        return {
            "failed": failed.data or [],
            "stale_pending": stale.data or [],
            "total_failed": len(failed.data or []),
            "total_stale": len(stale.data or []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Error Logs ────────────────────────────────────────────────────────────


@router.get("/error-log")
async def get_error_log():
    """Get recent errors across all agents — actions with error_message set."""
    try:
        sb = get_supabase()
        from datetime import datetime, timezone, timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()

        result = sb.table("agent_actions") \
            .select("action_id,agent_name,title,action_type,error_message,status,created_at,executed_at") \
            .neq("error_message", None) \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()

        # Also get failed OODA cycles
        cycles = sb.table("agent_cycles") \
            .select("agent_name,status,error_message,created_at") \
            .eq("status", "failed") \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()

        return {
            "action_errors": result.data or [],
            "cycle_errors": cycles.data or [],
            "total_action_errors": len(result.data or []),
            "total_cycle_errors": len(cycles.data or []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GitHub ────────────────────────────────────────────────────────────────


@router.get("/github/commits")
async def github_recent_commits(repo: Optional[str] = None, limit: int = 15):
    """Get recent commits across repos."""
    from shared.github_client import get_recent_commits
    try:
        return {"commits": await get_recent_commits(repo=repo, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/github/prs")
async def github_open_prs(repo: Optional[str] = None):
    """Get open pull requests."""
    from shared.github_client import get_open_prs
    try:
        prs = await get_open_prs(repo=repo)
        return {"pull_requests": prs, "total": len(prs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/github/issues")
async def github_open_issues(repo: Optional[str] = None, limit: int = 15):
    """Get open issues."""
    from shared.github_client import get_open_issues
    try:
        issues = await get_open_issues(repo=repo, limit=limit)
        return {"issues": issues, "total": len(issues)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/github/deploys")
async def github_recent_deploys(repo: Optional[str] = None, limit: int = 5):
    """Get recent deployments."""
    from shared.github_client import get_recent_deployments
    try:
        deploys = await get_recent_deployments(repo=repo, limit=limit)
        return {"deployments": deploys, "total": len(deploys)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/github/summary")
async def github_repo_summary():
    """Get a summary of all repos."""
    from shared.github_client import get_repo_summary
    try:
        return await get_repo_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── FORGE Action Tools ───────────────────────────────────────────────────
# These let FORGE actually FIX things, not just report on them.


class ExecuteActionRequest(BaseModel):
    action_id: str
    agent_name: str


@router.post("/actions/execute")
async def execute_action(request: ExecuteActionRequest):
    """
    Actually execute a pending action by calling the agent's /execute endpoint.
    This is what turns FORGE from a reporter into a fixer.
    """
    try:
        sb = get_supabase()

        # Fetch the action
        result = sb.table("agent_actions") \
            .select("*") \
            .eq("action_id", request.action_id) \
            .single() \
            .execute()

        if not result.data:
            return {"success": False, "error": f"Action {request.action_id} not found"}

        action = result.data
        agent = request.agent_name or action.get("agent_name", "")

        # Map agent → execute endpoint
        execute_routes = {
            "seo": "/api/seo/execute",
            "content": "/api/content/execute",
            "ads": "/api/ads/execute",
            "social": "/api/social/execute",
            "reviews": "/api/reviews/execute",
        }

        route = execute_routes.get(agent)
        if not route:
            return {"success": False, "error": f"No execute endpoint for agent: {agent}"}

        # Build the payload — include the DB row id so the execute endpoint can mark it done
        payload = {
            "id": action.get("action_id"),
            "action_type": action.get("action_type", ""),
            "type": action.get("action_type", ""),
            "title": action.get("title", ""),
            "description": action.get("description", ""),
            "keyword": action.get("metadata", {}).get("keyword", "") if isinstance(action.get("metadata"), dict) else "",
            "db_id": action.get("action_id"),
            **({k: v for k, v in (action.get("metadata") or {}).items()} if isinstance(action.get("metadata"), dict) else {}),
        }

        import httpx
        from shared.config import settings
        async with httpx.AsyncClient(base_url=settings.SAMA_API_URL, timeout=60.0) as client:
            resp = await client.post(route, json=payload)
            resp_data = resp.json()

        if resp_data.get("success"):
            logger.info(f"[forge] Executed action {request.action_id} for {agent}: success")
        else:
            logger.warning(f"[forge] Executed action {request.action_id} for {agent}: {resp_data}")

        return {
            "success": resp_data.get("success", False),
            "action_id": request.action_id,
            "agent": agent,
            "result": resp_data,
        }

    except Exception as e:
        logger.error(f"[forge] execute_action failed: {e}")
        return {"success": False, "error": str(e)}


class PublishDraftsRequest(BaseModel):
    agent_name: Optional[str] = None
    limit: int = 10


@router.post("/publish-drafts")
async def publish_drafts(request: PublishDraftsRequest):
    """
    Publish draft content — changes status from 'draft' to 'published'.
    Works on content_pieces and social_posts tables.
    """
    from datetime import datetime, timezone
    try:
        sb = get_supabase()
        published = []
        now = datetime.now(timezone.utc).isoformat()

        # Publish content drafts
        if not request.agent_name or request.agent_name == "content":
            try:
                drafts = sb.table("content_pieces") \
                    .select("id,title,content_type,status") \
                    .eq("status", "draft") \
                    .limit(request.limit) \
                    .execute()
                for d in (drafts.data or []):
                    sb.table("content_pieces") \
                        .update({"status": "published"}) \
                        .eq("id", d["id"]) \
                        .execute()
                    published.append({"table": "content_pieces", "id": d["id"], "title": d.get("title", "")})
            except Exception as e:
                logger.debug(f"[forge] content_pieces publish error: {e}")

        # Publish social drafts
        if not request.agent_name or request.agent_name == "social":
            try:
                drafts = sb.table("social_posts") \
                    .select("id,platform,content,status") \
                    .eq("status", "draft") \
                    .limit(request.limit) \
                    .execute()
                for d in (drafts.data or []):
                    sb.table("social_posts") \
                        .update({"status": "published", "published_at": now}) \
                        .eq("id", d["id"]) \
                        .execute()
                    published.append({"table": "social_posts", "id": d["id"], "platform": d.get("platform", "")})
            except Exception as e:
                logger.debug(f"[forge] social_posts publish error: {e}")

        return {
            "success": True,
            "published_count": len(published),
            "published": published,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


class CreateIssueRequest(BaseModel):
    title: str
    body: str
    repo: Optional[str] = None
    labels: Optional[List[str]] = None


@router.post("/github/issues/create")
async def create_github_issue(request: CreateIssueRequest):
    """Create a GitHub issue — FORGE can file bugs for problems it finds."""
    from shared.config import settings
    import httpx

    token = settings.GITHUB_TOKEN
    if not token:
        return {"success": False, "error": "GITHUB_TOKEN not configured"}

    owner = settings.GITHUB_OWNER
    repos = [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]
    repo = request.repo or (repos[0] if repos else "sama-agent")

    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "title": request.title,
        "body": request.body,
    }
    if request.labels:
        payload["labels"] = request.labels

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "success": True,
                "issue_number": data["number"],
                "url": data["html_url"],
            }
        return {
            "success": False,
            "error": f"GitHub API {resp.status_code}",
            "detail": resp.text[:300],
        }


class RunSchedulerJobRequest(BaseModel):
    job_name: str


@router.post("/scheduler/run-now")
async def run_scheduler_job_now(request: RunSchedulerJobRequest, background_tasks: BackgroundTasks):
    """Manually trigger a scheduler job immediately instead of waiting for the cron schedule."""
    from shared import scheduler as job_scheduler

    job_map = {
        "daily_keyword_tracking": job_scheduler._run_daily_keyword_tracking,
        "weekly_seo_audit": job_scheduler._run_weekly_seo_audit,
        "daily_workflow": job_scheduler._run_daily_workflow,
        "daily_metrics": job_scheduler._run_daily_metrics,
        "daily_ads_check": job_scheduler._run_daily_ads_check,
        "weekly_content_analysis": job_scheduler._run_weekly_content_analysis,
        "weekly_ai_visibility": job_scheduler._run_weekly_ai_visibility,
        "midday_review_check": job_scheduler._run_midday_review_check,
        "daily_reflection": job_scheduler._run_daily_reflection,
        "daily_digest": job_scheduler._run_daily_digest,
        "daily_agent_reports": job_scheduler._run_daily_agent_reports,
        "daily_dev_health_check": job_scheduler._run_daily_dev_health_check,
        "weekly_goal_review": job_scheduler._run_weekly_goal_review,
    }

    func = job_map.get(request.job_name)
    if not func:
        return {"success": False, "error": f"Unknown job: {request.job_name}. Available: {list(job_map.keys())}"}

    background_tasks.add_task(func)
    logger.info(f"[forge] Manually triggered scheduler job: {request.job_name}")
    return {"success": True, "job": request.job_name, "message": f"Job '{request.job_name}' started in background."}
