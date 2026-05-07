"""
POST /api/content/plan/create-from-analysis

The Skapa content-plan button on /c/analysis posts an analysis_run_id +
the chosen articles-per-week + the chosen social platforms here. We
delegate to agents.content_plan_creator and return the counts the
dashboard needs for its toast.

The heavy work (Claude calls, scrapes, drafts) runs in a background task
so the HTTP response stays fast. The dashboard polls /api/content/plan
to see the new rows appear in the calendar.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from agents.content_plan_creator import (
    SUPPORTED_PLATFORMS,
    create_plan_from_analysis,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateFromAnalysisPayload(BaseModel):
    analysis_run_id: str
    articles_per_week: int = Field(default=2, ge=1, le=5)
    social_platforms: List[str] = Field(default_factory=list)


@router.post("/plan/create-from-analysis", status_code=status.HTTP_202_ACCEPTED)
async def plan_create_from_analysis(payload: CreateFromAnalysisPayload, request: Request):
    """Kick off content-plan generation from a completed analysis run."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    if not tenant_id or tenant_id == "default":
        raise HTTPException(
            status_code=400,
            detail="tenant context required (X-Tenant-ID header)",
        )

    # Validate platforms client-side; tolerate unknown values by dropping them
    requested = [p.lower().strip() for p in payload.social_platforms if p]
    invalid = [p for p in requested if p not in SUPPORTED_PLATFORMS]
    if invalid:
        logger.warning(
            f"plan_create_from_analysis: dropping unsupported platforms {invalid}"
        )
    platforms = [p for p in requested if p in SUPPORTED_PLATFORMS]

    async def _run():
        try:
            result = await create_plan_from_analysis(
                tenant_id=tenant_id,
                analysis_run_id=payload.analysis_run_id,
                articles_per_week=payload.articles_per_week,
                social_platforms=platforms,
            )
            logger.info(
                f"plan_create_from_analysis done for tenant={tenant_id}: {result}"
            )
        except Exception as e:
            logger.exception(
                f"plan_create_from_analysis failed for tenant={tenant_id}: {e}"
            )

    asyncio.create_task(_run())

    return {
        "accepted": True,
        "analysis_run_id": payload.analysis_run_id,
        "articles_per_week": payload.articles_per_week,
        "social_platforms": platforms,
        "message": (
            f"Skapar plan: cirka {payload.articles_per_week * 13} artiklar "
            f"+ {payload.articles_per_week * 13 * len(platforms)} sociala inlägg "
            "genereras i bakgrunden. Kalendern uppdateras när de är klara."
        ),
    }
