"""
Strategy API — exposes the cross-channel StrategyAgent.

Endpoints:
- GET   /api/strategy/current     — latest active strategy for the tenant
- PATCH /api/strategy/current     — edit fields on the active strategy
- GET   /api/strategy/history     — recent strategies (metadata only)
- GET   /api/strategy/evaluation  — outcome correlation per topic
- POST  /api/strategy/generate    — synthesise a new strategy now
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.strategy import StrategyAgent
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


async def _agent_for_request(request: Request) -> StrategyAgent:
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)
    return StrategyAgent(tenant_config=config)


class GenerateRequest(BaseModel):
    horizon: Optional[str] = "quarterly"  # 'monthly' | 'quarterly' | 'annual'


class UpdateRequest(BaseModel):
    """Free-form patch — any provided keys are merged into the active strategy."""

    headline: Optional[str] = None
    verdict: Optional[str] = None
    horizon: Optional[str] = None
    executive_summary: Optional[str] = None
    north_star_metric: Optional[Any] = None
    domain_strategies: Optional[Any] = None
    cross_channel_priorities: Optional[Any] = None
    roadmap: Optional[Any] = None
    risks: Optional[Any] = None

    def to_patch(self) -> Dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


@router.get("/current")
async def get_current_strategy(request: Request):
    agent = await _agent_for_request(request)
    current = await agent.get_current()
    if not current:
        return {"strategy": None, "message": "No strategy generated yet."}
    return {"strategy": current}


@router.patch("/current")
async def patch_current_strategy(payload: UpdateRequest, request: Request):
    """Edit fields on the active strategy (S1 — inline document edit)."""
    agent = await _agent_for_request(request)
    patch = payload.to_patch()
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Whitelist verdict values to keep the badge logic stable.
    verdict = patch.get("verdict")
    if verdict and verdict not in {"critical", "weak", "improving", "strong"}:
        raise HTTPException(
            status_code=400,
            detail="verdict must be one of: critical, weak, improving, strong",
        )
    horizon = patch.get("horizon")
    if horizon and horizon not in {"monthly", "quarterly", "annual"}:
        raise HTTPException(
            status_code=400,
            detail="horizon must be one of: monthly, quarterly, annual",
        )
    updated = await agent.update_section(patch)
    if not updated:
        raise HTTPException(status_code=404, detail="No active strategy to update")
    return {"strategy": updated}


@router.get("/history")
async def get_strategy_history(request: Request, limit: int = 10):
    agent = await _agent_for_request(request)
    return {"strategies": await agent.list_history(limit=limit)}


@router.get("/evaluation")
async def get_strategy_evaluation(request: Request):
    """Per-topic outcome metrics (S2 — cyclic strategy evaluation)."""
    agent = await _agent_for_request(request)
    return await agent.evaluate()


@router.post("/generate")
async def generate_strategy(payload: GenerateRequest, request: Request):
    agent = await _agent_for_request(request)
    horizon = payload.horizon or "quarterly"
    if horizon not in {"monthly", "quarterly", "annual"}:
        raise HTTPException(status_code=400, detail="horizon must be monthly|quarterly|annual")
    result = await agent.generate_strategy(horizon=horizon)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"strategy": result}
