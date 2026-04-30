"""Per-tenant usage and plan-limits API."""

import logging
from fastapi import APIRouter, HTTPException, Request

from shared.usage import get_usage_summary

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
async def get_usage(request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    if not tenant_id or tenant_id == "default":
        raise HTTPException(status_code=400, detail="Tenant ID required")
    try:
        return await get_usage_summary(tenant_id)
    except Exception as e:
        logger.error(f"usage lookup failed for {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
