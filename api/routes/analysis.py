"""
Analysis API routes — SEO + GEO unified visibility (P2.9).

Endpoints (all tenant-scoped via X-Tenant-ID header):
  POST /api/analysis/run              kick off a new analysis (async)
  POST /api/analysis/generate-queries get LLM-suggested queries
  GET  /api/analysis/runs             list persisted runs (history view)
  GET  /api/analysis/runs/{id}        get one run (status polling + replay)

The dashboard's /api/analysis/run Next.js route proxies here when
ANALYSIS_REAL=1 in the deploy env. Until that flag flips it serves
deterministic mock data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


class GenerateQueriesPayload(BaseModel):
    count: int = 10


class RunPayload(BaseModel):
    queries: List[str]
    platforms: Optional[List[str]] = None
    # Optional overrides — when the user just typed these on the analysis
    # page they should win over whatever's stored in tenant settings.
    brand_name: Optional[str] = None
    domain: Optional[str] = None
    competitors: Optional[List[str]] = None


# ── POST /generate-queries ───────────────────────────────────────────────────

@router.post("/generate-queries")
async def generate_queries(payload: GenerateQueriesPayload, request: Request):
    """LLM-suggest buyer-intent queries based on tenant brand context."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)
    from agents.analysis import AnalysisAgent
    agent = AnalysisAgent(tenant_config=config)
    queries = await agent.generate_queries(count=max(1, min(payload.count, 25)))
    return {"queries": queries}


# ── POST /run ────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_analysis(payload: RunPayload, request: Request):
    """
    Kick off an analysis. Persists a row immediately, runs the orchestration
    in the background, and returns the row id so the dashboard can poll
    /runs/{id}.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    queries = [q.strip() for q in (payload.queries or []) if q and q.strip()]
    if not queries:
        raise HTTPException(status_code=400, detail="queries required")
    if len(queries) > 25:
        raise HTTPException(status_code=400, detail="max 25 queries per run")

    platforms = payload.platforms or ["chatgpt", "claude", "perplexity", "google_aio"]

    config = await get_tenant_config(tenant_id)
    sb = get_supabase()

    # Apply per-request overrides (what the user just typed) over the
    # stored tenant config. This is what gets persisted on the run row
    # AND what the agent uses for crawling/mention detection.
    brand_name = (payload.brand_name or getattr(config, "brand_name", None) or "").strip() or None
    domain = (payload.domain or getattr(config, "domain", None) or "").strip() or None
    competitors = payload.competitors if payload.competitors is not None else (
        list(getattr(config, "competitors", []) or [])
    )

    # Persist the row before kicking off the background task — if the insert
    # fails (RLS, missing table, bad credentials) the dashboard has no id to
    # poll, so surface the real reason instead of returning {"id": null}.
    try:
        ins = sb.table("analysis_runs").insert({
            "tenant_id": tenant_id,
            "brand_name": brand_name,
            "domain": domain,
            "query_count": len(queries),
            "platform_count": len(platforms),
            "status": "running",
        }).execute()
    except Exception as e:
        logger.exception("Could not insert analysis_runs row")
        raise HTTPException(
            status_code=500,
            detail=f"Could not create analysis run row: {e}",
        )

    if not ins.data:
        raise HTTPException(
            status_code=500,
            detail="analysis_runs insert returned no row (check RLS policy for analysis_runs)",
        )
    run_id = ins.data[0]["id"]

    asyncio.create_task(_execute_analysis(
        run_id, tenant_id, queries, platforms, brand_name, domain, competitors,
    ))

    return {"id": run_id, "status": "running"}


async def _execute_analysis(
    run_id: str,
    tenant_id: str,
    queries: List[str],
    platforms: List[str],
    brand_name: Optional[str] = None,
    domain: Optional[str] = None,
    competitors: Optional[List[str]] = None,
) -> None:
    """Background task: runs the analysis and updates the row."""
    from agents.analysis import AnalysisAgent
    sb = get_supabase()
    config = await get_tenant_config(tenant_id)
    agent = AnalysisAgent(tenant_config=config)
    if brand_name:
        agent.brand_name = brand_name
    if domain:
        agent.domain = domain
    if competitors is not None:
        agent.competitors = list(competitors)

    try:
        result = await agent.run(queries, platforms)
        result["id"] = run_id
        update = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "payload": result,
        }
    except Exception as e:
        logger.exception(f"Analysis run {run_id} failed for tenant {tenant_id}")
        update = {
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }

    try:
        sb.table("analysis_runs").update(update).eq("id", run_id).execute()
    except Exception:
        logger.warning(f"Could not persist analysis_run {run_id} update", exc_info=True)


# ── GET /runs ────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(request: Request, limit: int = 20):
    """Recent analysis runs for this tenant (for the history view)."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("analysis_runs")
            .select("id,brand_name,domain,query_count,platform_count,status,started_at,completed_at,error")
            .eq("tenant_id", tenant_id)
            .order("started_at", desc=True)
            .limit(min(limit, 100))
            .execute()
        )
        return {"runs": result.data or []}
    except Exception as e:
        logger.error(f"list analysis_runs failed: {e}")
        return {"runs": []}


# ── GET /runs/{id} ───────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> Dict[str, Any]:
    """One run with its full payload — used for polling and replay."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("analysis_runs")
            .select("*")
            .eq("id", run_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        row = rows[0]
        # If the run is complete, surface the AnalysisRun payload directly so
        # the frontend can render it without reshaping.
        if row.get("status") == "completed" and row.get("payload"):
            payload = row["payload"]
            payload["id"] = row["id"]
            payload["status"] = row["status"]
            return payload
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get analysis_run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
