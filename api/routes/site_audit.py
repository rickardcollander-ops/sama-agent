"""
Site Audit API routes — full-domain SEO + GEO + technical + link audit.

Endpoints (all tenant-scoped via X-Tenant-ID or JWT):
  POST /api/site-audit/run         kick off a new audit (async background task)
  GET  /api/site-audit/runs        list persisted audits (history view)
  GET  /api/site-audit/runs/{id}   get one audit (status polling + replay)

Mirrors the shape of /api/analysis: insert a row, run the audit in the
background, dashboard polls for completion.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


class RunPayload(BaseModel):
    domain: Optional[str] = None
    max_pages: Optional[int] = 15


# ── POST /run ────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_site_audit(payload: RunPayload, request: Request):
    """
    Kick off a site audit. Persists a row immediately, runs the crawl in the
    background, and returns the row id so the dashboard can poll /runs/{id}.
    Falls back to the tenant's configured domain if `domain` is not supplied.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)

    domain = (payload.domain or config.domain or "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="domain required")

    max_pages = max(1, min(payload.max_pages or 15, 30))
    sb = get_supabase()

    run_id = None
    try:
        ins = sb.table("site_audits").insert({
            "tenant_id": tenant_id,
            "domain": domain,
            "status": "running",
        }).execute()
        if ins.data:
            run_id = ins.data[0]["id"]
    except Exception as e:
        logger.warning(f"Could not insert site_audits row: {e}")

    asyncio.create_task(_execute_audit(run_id, tenant_id, domain, max_pages))

    return {"id": run_id, "status": "running"}


async def _execute_audit(
    run_id: Optional[str],
    tenant_id: str,
    domain: str,
    max_pages: int,
) -> None:
    """Background task: runs the audit and updates the row."""
    from agents.site_audit import SiteAuditAgent
    sb = get_supabase()
    config = await get_tenant_config(tenant_id)
    agent = SiteAuditAgent(tenant_config=config)

    try:
        result = await agent.audit_domain(domain=domain, max_pages=max_pages)
        if run_id:
            result["id"] = run_id
        update = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pages_analyzed": result.get("summary", {}).get("pages_analyzed", 0),
            "overall_score": result.get("scores", {}).get("overall"),
            "payload": result,
        }
    except Exception as e:
        logger.exception(f"Site audit {run_id} failed for tenant {tenant_id}")
        update = {
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }

    if run_id:
        try:
            sb.table("site_audits").update(update).eq("id", run_id).execute()
        except Exception:
            logger.warning(f"Could not persist site_audit {run_id} update", exc_info=True)


# ── GET /runs ────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(request: Request, limit: int = 20):
    """Recent audits for this tenant (history view)."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("site_audits")
            .select("id,domain,pages_analyzed,overall_score,status,started_at,completed_at,error")
            .eq("tenant_id", tenant_id)
            .order("started_at", desc=True)
            .limit(min(limit, 100))
            .execute()
        )
        return {"runs": result.data or []}
    except Exception as e:
        logger.error(f"list site_audits failed: {e}")
        return {"runs": []}


# ── GET /runs/{id} ───────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> Dict[str, Any]:
    """One audit with its full payload — used for polling and replay."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("site_audits")
            .select("*")
            .eq("id", run_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Site audit not found")
        row = rows[0]
        if row.get("status") == "completed" and row.get("payload"):
            payload = row["payload"]
            payload["id"] = row["id"]
            payload["status"] = row["status"]
            payload["created_at"] = row.get("started_at")
            return payload
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get site_audit failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
