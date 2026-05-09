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
import time
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
    max_pages: Optional[int] = 200


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

    max_pages = max(1, min(payload.max_pages or 200, 500))
    sb = get_supabase()

    # Persist the row before kicking off the background task — if the insert
    # fails (RLS, missing table, bad credentials) the dashboard has no id to
    # poll, so surface the real reason instead of returning {"id": null}.
    try:
        ins = sb.table("site_audits").insert({
            "tenant_id": tenant_id,
            "domain": domain,
            "status": "running",
        }).execute()
    except Exception as e:
        logger.exception("Could not insert site_audits row")
        raise HTTPException(
            status_code=500,
            detail=f"Could not create site audit row: {e}",
        )

    if not ins.data:
        raise HTTPException(
            status_code=500,
            detail="site_audits insert returned no row (check RLS policy for site_audits)",
        )
    run_id = ins.data[0]["id"]

    asyncio.create_task(_execute_audit(run_id, tenant_id, domain, max_pages))

    return {"id": run_id, "status": "running"}


# How often (in seconds) we may write progress to Supabase. The crawler
# fires the callback after every page; on a fast site that's many writes
# per second. Throttling keeps DB load sane while still feeling live.
_PROGRESS_WRITE_INTERVAL_S = 1.5

# Tracks whether the optional progress columns (pages_total / pages_done from
# migration 036_site_audit_progress.sql) exist in the connected database.
# We probe lazily on the first failure with code 42703 and then fall back to
# a slimmer SELECT so we don't spam the error log for every dashboard poll.
_PROGRESS_COLUMNS_PRESENT: Optional[bool] = None

# Same probe-and-cache trick for the AI-readability columns added in
# migration 042_ai_readability.sql. If the migration hasn't been applied
# yet, we silently drop those keys from the update and the audit still
# completes successfully.
_AI_READABILITY_COLUMNS_PRESENT: Optional[bool] = None


def _is_missing_column_error(err: Exception) -> bool:
    """True when Supabase tells us a SELECT references a non-existent column."""
    code = getattr(err, "code", None)
    if code == "42703":
        return True
    msg = str(err)
    return "42703" in msg or "does not exist" in msg and "column" in msg


async def _execute_audit(
    run_id: str,
    tenant_id: str,
    domain: str,
    max_pages: int,
) -> None:
    """Background task: runs the audit and updates the row."""
    from agents.site_audit import SiteAuditAgent
    sb = get_supabase()
    config = await get_tenant_config(tenant_id)
    agent = SiteAuditAgent(tenant_config=config)

    last_write = {"t": 0.0, "total": 0}

    async def progress_cb(done: int, total: int) -> None:
        # Always write the first tick so the widget shows pages_total fast,
        # then throttle, but always flush the final tick so the bar reaches
        # 100% as soon as the crawl loop returns.
        now = time.monotonic()
        first = last_write["total"] == 0 and total > 0
        final = total > 0 and done >= total
        if not (first or final or now - last_write["t"] >= _PROGRESS_WRITE_INTERVAL_S):
            return
        last_write["t"] = now
        last_write["total"] = total
        global _PROGRESS_COLUMNS_PRESENT
        if _PROGRESS_COLUMNS_PRESENT is False:
            return
        try:
            await asyncio.to_thread(
                lambda: sb.table("site_audits").update({
                    "pages_total": total,
                    "pages_done": done,
                }).eq("id", run_id).execute()
            )
            _PROGRESS_COLUMNS_PRESENT = True
        except Exception as e:
            if _is_missing_column_error(e):
                _PROGRESS_COLUMNS_PRESENT = False
                logger.warning(
                    "site_audits.pages_total/pages_done missing — apply "
                    "migration 036_site_audit_progress.sql to enable progress UI"
                )
            else:
                logger.debug(f"progress write failed for {run_id}", exc_info=True)

    try:
        result = await agent.audit_domain(
            domain=domain, max_pages=max_pages, progress_cb=progress_cb,
        )
        result["id"] = run_id

        # AI-readability post-step. Runs against the audit result, refetches
        # the homepage + 2 most-linked pages, and writes the report into
        # ``payload.ai_readability``. Best-effort: failures are logged but
        # never fail the audit.
        try:
            from agents.ai_readability import score_audit
            from shared.config import settings as _settings
            anth_key = (
                getattr(config, "anthropic_api_key", None)
                or _settings.ANTHROPIC_API_KEY
            )
            ai_readability = await score_audit(
                result,
                anthropic_key=anth_key,
                tenant_id=tenant_id,
            )
            result["ai_readability"] = ai_readability
        except Exception as e:
            logger.info(f"ai_readability scoring skipped for {run_id}: {e}")
            result["ai_readability"] = {"error": str(e)[:240]}

        update = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pages_analyzed": result.get("summary", {}).get("pages_analyzed", 0),
            "overall_score": result.get("scores", {}).get("overall"),
            "payload": result,
        }
        # Top up the AI-readability score columns when migration 042 has run.
        ai_block = result.get("ai_readability") or {}
        ai_score = ai_block.get("overall_score")
        if isinstance(ai_score, (int, float)):
            update["ai_readability_score"] = int(ai_score)
            update["ai_readability_run_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.exception(f"Site audit {run_id} failed for tenant {tenant_id}")
        update = {
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }

    global _AI_READABILITY_COLUMNS_PRESENT
    try:
        sb.table("site_audits").update(update).eq("id", run_id).execute()
        if "ai_readability_score" in update:
            _AI_READABILITY_COLUMNS_PRESENT = True
    except Exception as e:
        if (
            "ai_readability_score" in update
            and _is_missing_column_error(e)
        ):
            _AI_READABILITY_COLUMNS_PRESENT = False
            logger.warning(
                "site_audits.ai_readability_* missing — apply migration "
                "042_ai_readability.sql to enable AI-readability scorecard"
            )
            update.pop("ai_readability_score", None)
            update.pop("ai_readability_run_at", None)
            try:
                sb.table("site_audits").update(update).eq("id", run_id).execute()
            except Exception:
                logger.warning(
                    f"Could not persist site_audit {run_id} update (fallback)",
                    exc_info=True,
                )
        else:
            logger.warning(
                f"Could not persist site_audit {run_id} update", exc_info=True
            )


# ── GET /runs ────────────────────────────────────────────────────────────────

_FULL_RUN_COLUMNS = (
    "id,domain,pages_analyzed,pages_total,pages_done,"
    "overall_score,status,started_at,completed_at,error"
)
_LEGACY_RUN_COLUMNS = (
    "id,domain,pages_analyzed,overall_score,status,"
    "started_at,completed_at,error"
)


@router.get("/runs")
async def list_runs(request: Request, limit: int = 20):
    """Recent audits for this tenant (history view)."""
    global _PROGRESS_COLUMNS_PRESENT
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    columns = (
        _LEGACY_RUN_COLUMNS
        if _PROGRESS_COLUMNS_PRESENT is False
        else _FULL_RUN_COLUMNS
    )

    def _query(cols: str):
        return (
            sb.table("site_audits")
            .select(cols)
            .eq("tenant_id", tenant_id)
            .order("started_at", desc=True)
            .limit(min(limit, 100))
            .execute()
        )

    try:
        result = _query(columns)
        if _PROGRESS_COLUMNS_PRESENT is None:
            _PROGRESS_COLUMNS_PRESENT = True
        return {"runs": result.data or []}
    except Exception as e:
        if _PROGRESS_COLUMNS_PRESENT is not False and _is_missing_column_error(e):
            _PROGRESS_COLUMNS_PRESENT = False
            logger.warning(
                "site_audits progress columns missing — falling back to "
                "legacy SELECT. Apply migration 036_site_audit_progress.sql."
            )
            try:
                result = _query(_LEGACY_RUN_COLUMNS)
                return {"runs": result.data or []}
            except Exception as e2:
                logger.error(f"list site_audits failed (legacy): {e2}")
                return {"runs": []}
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
