"""
POST /api/content/plan/create-from-analysis

The Skapa content-plan button on /c/analysis posts an analysis_run_id +
the chosen articles-per-week + the chosen social platforms here. We
delegate to agents.content_plan_creator and return the counts the
dashboard needs for its toast.

The heavy work (Claude calls, scrapes, drafts) runs in a background task
so the HTTP response stays fast. The dashboard polls /api/content/plan
to see the new rows appear in the calendar.

We also write an agent_runs row (agent_name="content_plan") so the
dashboard's bottom-right active-runs widget can track progress and show
a completion summary alongside the other agents.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from agents.content_plan_creator import (
    SUPPORTED_PLATFORMS,
    create_plan_from_analysis,
)
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateFromAnalysisPayload(BaseModel):
    analysis_run_id: str
    articles_per_week: int = Field(default=2, ge=1, le=5)
    social_platforms: List[str] = Field(default_factory=list)
    # Optional inline copy of the analysis run. The dashboard sends these
    # when it's working from a locally-cached run that the backend may not
    # have anymore (saved_analyses_by_tenant fallback). When present we use
    # them directly and skip the analysis_runs DB lookup.
    analysis_payload: Optional[Dict[str, Any]] = None
    analysis_domain: Optional[str] = None
    analysis_brand_name: Optional[str] = None


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

    # Default social cadence to articles cadence so old clients keep the
    # 1-social-per-article behaviour. 0 means the user explicitly opted out.
    social_per_week = (
        payload.articles_per_week
        if payload.social_posts_per_week is None
        else payload.social_posts_per_week
    )

    sb = get_supabase()
    run_id: Optional[str] = None
    try:
        run_result = sb.table("agent_runs").insert({
            "tenant_id": tenant_id,
            "agent_name": "content_plan",
            "status": "running",
        }).execute()
        if run_result.data:
            run_id = run_result.data[0].get("id")
    except Exception as e:
        logger.warning(f"Could not record content_plan agent run: {e}")

    async def _run():
        status_value = "completed"
        summary = ""
        error_msg: Optional[str] = None
        try:
            result = await create_plan_from_analysis(
                tenant_id=tenant_id,
                analysis_run_id=payload.analysis_run_id,
                articles_per_week=payload.articles_per_week,
                social_platforms=platforms,
                analysis_payload=payload.analysis_payload,
                analysis_domain=payload.analysis_domain,
                analysis_brand_name=payload.analysis_brand_name,
            )
            logger.info(
                f"plan_create_from_analysis done for tenant={tenant_id}: {result}"
            )
            articles = int(result.get("articles_created") or 0)
            socials = int(result.get("social_posts_created") or 0)
            warning = result.get("warning")
            if warning:
                summary = f"Plan klar: {articles} artiklar, {socials} sociala inlägg ({warning})"
            else:
                summary = f"Plan klar: {articles} artiklar, {socials} sociala inlägg"
        except Exception as e:
            logger.exception(
                f"plan_create_from_analysis failed for tenant={tenant_id}: {e}"
            )
            status_value = "failed"
            error_msg = str(e)[:500]

        if run_id:
            try:
                update = {
                    "status": status_value,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                }
                if error_msg:
                    update["error"] = error_msg
                sb.table("agent_runs").update(update).eq("id", run_id).execute()
            except Exception:
                logger.warning(
                    f"Could not finalise agent_runs row {run_id} for content_plan",
                    exc_info=True,
                )

    asyncio.create_task(_run())

    total_articles = payload.articles_per_week * 13
    total_socials = social_per_week * 13 * len(platforms)
    return {
        "accepted": True,
        "run_id": run_id,
        "analysis_run_id": payload.analysis_run_id,
        "articles_per_week": payload.articles_per_week,
        "social_posts_per_week": social_per_week,
        "social_platforms": platforms,
        "message": (
            f"Skapar plan: cirka {total_articles} artiklar "
            f"+ {total_socials} sociala inlägg "
            "genereras i bakgrunden. Kalendern uppdateras när de är klara."
        ),
    }
