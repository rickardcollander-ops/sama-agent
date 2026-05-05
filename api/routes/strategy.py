"""
Strategy API — exposes the cross-channel StrategyAgent.

Endpoints:
- GET  /api/strategy/current   — latest active strategy for the tenant
- GET  /api/strategy/history   — recent strategies (metadata only)
- POST /api/strategy/generate  — kick off generation in background, return run_id
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.strategy import StrategyAgent
from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


async def _agent_for_request(request: Request) -> StrategyAgent:
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)
    return StrategyAgent(tenant_config=config)


class GenerateRequest(BaseModel):
    horizon: Optional[str] = "quarterly"  # 'monthly' | 'quarterly' | 'annual'


# ── Shape normalisation ─────────────────────────────────────────────────────
#
# The DB row stores headline/verdict/horizon/generated_at as columns and the
# rest of the AI output inside a `strategy` JSONB blob. The dashboard reads
# every field at the top level of the response, so we flatten the row before
# returning it. We also coerce the two fields whose shape has historically
# drifted (domain_strategies, roadmap) into arrays — older saved strategies
# used objects keyed by domain or by 30/60/90-day buckets and the UI only
# iterates over arrays, so without this older entries render as a stub.


def _normalize_domain_strategies(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        out: List[Dict[str, Any]] = []
        for domain, body in value.items():
            if not isinstance(body, dict):
                continue
            out.append({
                "domain": domain,
                "diagnosis": body.get("diagnosis"),
                # Older prompts asked for "objective"; the UI reads "goal".
                "goal": body.get("goal") or body.get("objective"),
                "key_actions": body.get("key_actions") or body.get("actions") or [],
                "kpi": body.get("kpi"),
            })
        return out
    return []


def _normalize_roadmap(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        # Convert {"30_days": [...], "60_days": [...], "90_days": [...]} to
        # the list-of-milestones shape the UI expects.
        bucket_to_label = {
            "30_days": "30d", "30d": "30d", "30": "30d",
            "60_days": "60d", "60d": "60d", "60": "60d",
            "90_days": "90d", "90d": "90d", "90": "90d",
        }
        out: List[Dict[str, Any]] = []
        for raw_key, body in value.items():
            label = bucket_to_label.get(str(raw_key), str(raw_key))
            if isinstance(body, list):
                items = [str(x) for x in body if x]
                out.append({"horizon": label, "items": items})
            elif isinstance(body, dict):
                out.append({
                    "horizon": label,
                    "title": body.get("title"),
                    "description": body.get("description"),
                    "items": body.get("items") or [],
                })
        # Sort 30d → 60d → 90d when those labels are present.
        order = {"30d": 0, "60d": 1, "90d": 2}
        out.sort(key=lambda m: order.get(str(m.get("horizon", "")), 99))
        return out
    return []


def _flatten_strategy_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Merge the inner `strategy` JSON onto the row so the dashboard can read
    every field at the top level."""
    if not row:
        return None
    inner = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
    return {
        "id": row.get("id"),
        "generated_at": row.get("generated_at"),
        "horizon": row.get("horizon") or inner.get("horizon"),
        "status": row.get("status"),
        "contributing_agents": row.get("contributing_agents") or [],
        "headline": inner.get("headline") or row.get("headline"),
        "verdict": inner.get("verdict") or row.get("verdict"),
        "executive_summary": inner.get("executive_summary"),
        "domain_strategies": _normalize_domain_strategies(inner.get("domain_strategies")),
        "cross_channel_priorities": inner.get("cross_channel_priorities") or [],
        "roadmap": _normalize_roadmap(inner.get("roadmap")),
        "risks": inner.get("risks") or [],
        "north_star_metric": inner.get("north_star_metric"),
    }


@router.get("/current")
async def get_current_strategy(request: Request):
    agent = await _agent_for_request(request)
    current = await agent.get_current()
    if not current:
        return {"strategy": None, "message": "No strategy generated yet."}
    return {"strategy": _flatten_strategy_row(current)}


@router.get("/history")
async def get_strategy_history(request: Request, limit: int = 10):
    agent = await _agent_for_request(request)
    return {"strategies": await agent.list_history(limit=limit)}


def _finalize_run(run_id: Optional[str], status: str, summary: str = "", error: Optional[str] = None) -> None:
    if not run_id:
        return
    try:
        sb = get_supabase()
        update: Dict[str, Any] = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }
        if error:
            update["error"] = error[:500]
        sb.table("agent_runs").update(update).eq("id", run_id).execute()
    except Exception:
        logger.warning(f"Could not update agent_runs {run_id}", exc_info=True)


def _generate_strategy_thread(agent: StrategyAgent, horizon: str, run_id: Optional[str]) -> None:
    try:
        result = asyncio.run(agent.generate_strategy(horizon=horizon))
        if isinstance(result, dict) and "error" in result:
            _finalize_run(run_id, "failed", error=str(result["error"]))
            return
        flat = _flatten_strategy_row(result) or {}
        headline = (flat.get("headline") or "Strategy generated")[:200]
        _finalize_run(run_id, "completed", summary=headline)
    except Exception as e:
        logger.error(f"[strategy] background generation failed: {e}", exc_info=True)
        _finalize_run(run_id, "failed", error=str(e))


@router.post("/generate")
async def generate_strategy(payload: GenerateRequest, request: Request):
    """Kick off strategy generation in a background thread.

    Returns immediately with a run_id so the dashboard can poll
    /api/tenant/agent-runs/{run_id} for completion. The Claude call
    routinely takes 60-90s, which exceeds proxy/fetch timeouts when run
    inline — keeping it inline made the UI silently revert after a long
    wait with no user feedback.
    """
    horizon = payload.horizon or "quarterly"
    if horizon not in {"monthly", "quarterly", "annual"}:
        raise HTTPException(status_code=400, detail="horizon must be monthly|quarterly|annual")

    tenant_id = getattr(request.state, "tenant_id", "default")
    agent = await _agent_for_request(request)

    run_id: Optional[str] = None
    if tenant_id and tenant_id != "default":
        try:
            sb = get_supabase()
            insert = sb.table("agent_runs").insert({
                "tenant_id": tenant_id,
                "agent_name": "strategy",
                "status": "running",
            }).execute()
            if insert.data:
                run_id = insert.data[0].get("id")
        except Exception:
            logger.warning("Could not insert agent_runs row for strategy generation", exc_info=True)

    t = threading.Thread(
        target=_generate_strategy_thread,
        args=(agent, horizon, run_id),
        daemon=True,
        name=f"strategy-gen-{tenant_id}",
    )
    t.start()
    logger.info(f"[strategy] background generation started (tenant={tenant_id}, run_id={run_id}, horizon={horizon})")

    return {
        "status": "started",
        "run_id": run_id,
        "horizon": horizon,
        "message": "Strategy generation started. Poll /api/tenant/agent-runs/{run_id} for completion.",
    }
