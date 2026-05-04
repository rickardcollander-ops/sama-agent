"""
Strategy API — exposes the cross-channel StrategyAgent.

Endpoints:
- GET  /api/strategy/current   — latest active strategy for the tenant
- GET  /api/strategy/history   — recent strategies (metadata only)
- POST /api/strategy/generate  — synthesise a new strategy now
"""

import logging
from typing import Optional

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


@router.get("/current")
async def get_current_strategy(request: Request):
    agent = await _agent_for_request(request)
    current = await agent.get_current()
    if not current:
        return {"strategy": None, "message": "No strategy generated yet."}
    return {"strategy": current}


@router.get("/history")
async def get_strategy_history(request: Request, limit: int = 10):
    agent = await _agent_for_request(request)
    return {"strategies": await agent.list_history(limit=limit)}


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
