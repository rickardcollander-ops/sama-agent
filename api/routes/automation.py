"""
Automation Routes - Scheduled Tasks and Workflows
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel
from typing import Optional
import logging

from agents.seo import seo_agent
from agents.content import content_agent
from agents.social import social_agent
from agents.reviews import review_agent

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_tenant_id(request: Request) -> Optional[str]:
    """Extract tenant_id from request.state (set by TenantMiddleware)."""
    return getattr(request.state, "tenant_id", None)


async def _get_agents(tenant_id: Optional[str]):
    """Return tenant-scoped agents when a non-default tenant_id is provided,
    otherwise fall back to the global singleton agents."""
    if tenant_id and tenant_id != "default":
        from shared.tenant_agents import (
            get_seo_agent, get_content_agent, get_social_agent, get_review_agent,
        )
        return (
            await get_seo_agent(tenant_id),
            await get_content_agent(tenant_id),
            await get_social_agent(tenant_id),
            await get_review_agent(tenant_id),
        )
    return seo_agent, content_agent, social_agent, review_agent


class DailyWorkflowRequest(BaseModel):
    force: bool = False
    tenant_id: Optional[str] = None


@router.post("/daily-workflow")
async def run_daily_workflow(
    request: Request,
    body: DailyWorkflowRequest,
    background_tasks: BackgroundTasks,
):
    """
    Run daily automated workflow:
    1. SEO keyword tracking
    2. Review monitoring
    3. Social post generation
    4. Analytics update
    """
    # Prefer explicit body param, then middleware value
    tenant_id = body.tenant_id or _get_tenant_id(request)
    try:
        _seo, _content, _social, _review = await _get_agents(tenant_id)

        results = {
            "tenant_id": tenant_id,
            "seo": None,
            "reviews": None,
            "social": None,
            "timestamp": None
        }

        # Run SEO keyword tracking
        logger.info("Running daily SEO keyword tracking...")
        try:
            seo_result = await _seo.track_keyword_rankings()
            results["seo"] = {"status": "success", "keywords_tracked": len(seo_result)}
        except Exception as e:
            logger.error(f"SEO tracking failed: {e}")
            results["seo"] = {"status": "error", "message": str(e)}

        # Monitor reviews
        logger.info("Monitoring review platforms...")
        try:
            review_result = await _review.fetch_all_reviews()
            results["reviews"] = {"status": "success", "platforms_checked": len(review_result)}
        except Exception as e:
            logger.error(f"Review monitoring failed: {e}")
            results["reviews"] = {"status": "error", "message": str(e)}

        # Generate social post
        logger.info("Generating daily social post...")
        try:
            social_result = await _social.generate_post(
                topic="Daily CS insight or product update",
                style="educational"
            )
            results["social"] = {"status": "success", "post_generated": True}
        except Exception as e:
            logger.error(f"Social post generation failed: {e}")
            results["social"] = {"status": "error", "message": str(e)}

        from datetime import datetime
        results["timestamp"] = datetime.utcnow().isoformat()

        return {
            "success": True,
            "message": "Daily workflow completed",
            "results": results
        }

    except Exception as e:
        logger.error(f"Daily workflow failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/weekly-seo-audit")
async def run_weekly_seo_audit(request: Request, background_tasks: BackgroundTasks):
    """
    Run comprehensive weekly SEO audit:
    - Full site crawl
    - Keyword position tracking
    - Competitor analysis
    - Content gap identification
    """
    tenant_id = _get_tenant_id(request)
    try:
        _seo, _, _, _ = await _get_agents(tenant_id)
        background_tasks.add_task(_seo.run_weekly_audit)
        return {
            "success": True,
            "message": "Weekly SEO audit started in background"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content-generation-workflow")
async def run_content_generation_workflow(request: Request, background_tasks: BackgroundTasks):
    """
    Automated content generation workflow:
    1. Identify keyword opportunities from SEO agent
    2. Generate blog post for top opportunity
    3. Create social posts to promote content
    """
    tenant_id = _get_tenant_id(request)
    try:
        _seo, _content, _social, _ = await _get_agents(tenant_id)

        # Get keyword opportunities
        opportunities = await _seo.discover_keyword_opportunities()

        if not opportunities:
            return {
                "success": True,
                "message": "No keyword opportunities found",
                "content_generated": False
            }

        # Take top opportunity
        top_opportunity = opportunities[0]

        # Generate blog post
        blog_result = await _content.generate_blog_post(
            topic=f"Complete guide to {top_opportunity['keyword']}",
            target_keyword=top_opportunity['keyword'],
            word_count=2000,
            pillar="churn_prevention"  # Default pillar
        )

        # Generate social post to promote
        social_result = await _social.generate_post(
            topic=f"New blog post: {blog_result['title']}",
            style="announcement"
        )

        return {
            "success": True,
            "message": "Content generation workflow completed",
            "blog_post": {
                "title": blog_result.get("title"),
                "word_count": blog_result.get("word_count"),
                "status": blog_result.get("status")
            },
            "social_post": {
                "content": social_result.get("content")[:100] + "...",
                "platform": social_result.get("platform")
            }
        }

    except Exception as e:
        logger.error(f"Content generation workflow failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_automation_status():
    """Get real status of all automation workflows"""
    from shared.scheduler import get_job_history, scheduler

    history = get_job_history()
    running = scheduler.running

    def next_run(job_id: str) -> Optional[str]:
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    return {
        "scheduler_running": running,
        "workflows": {
            "daily_keyword_tracking": {
                "schedule": "Every day at 02:00 UTC",
                "next_run": next_run("daily_keyword_tracking"),
                **history["daily_keyword_tracking"],
            },
            "weekly_seo_audit": {
                "schedule": "Every Monday at 03:00 UTC",
                "next_run": next_run("weekly_seo_audit"),
                **history["weekly_seo_audit"],
            },
            "daily_workflow": {
                "schedule": "Every day at 06:00 UTC",
                "next_run": next_run("daily_workflow"),
                **history["daily_workflow"],
            },
        },
    }


@router.post("/trigger/keyword-tracking")
async def trigger_keyword_tracking():
    """Manually trigger keyword tracking right now"""
    from shared.scheduler import _run_daily_keyword_tracking
    try:
        await _run_daily_keyword_tracking()
        from shared.scheduler import get_job_history
        return {"success": True, **get_job_history()["daily_keyword_tracking"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger/seo-audit")
async def trigger_seo_audit(background_tasks: BackgroundTasks):
    """Manually trigger SEO audit in background"""
    from shared.scheduler import _run_weekly_seo_audit
    background_tasks.add_task(_run_weekly_seo_audit)
    return {"success": True, "message": "SEO audit started in background"}


@router.post("/content-refresh")
async def run_content_refresh_workflow():
    """
    Monthly content refresh workflow
    Identifies and refreshes content older than 30 days
    """
    try:
        from agents.content_advanced import advanced_content_generator

        # Identify content to refresh
        content_to_refresh = await advanced_content_generator.identify_content_for_refresh()

        if not content_to_refresh:
            return {
                "success": True,
                "message": "No content needs refreshing",
                "refreshed_count": 0
            }

        # Refresh top 5 oldest pieces
        refreshed = []
        for content in content_to_refresh[:5]:
            result = await advanced_content_generator.refresh_content(content["id"])
            if result.get("success"):
                refreshed.append(content["id"])

        return {
            "success": True,
            "message": f"Refreshed {len(refreshed)} content pieces",
            "refreshed_count": len(refreshed),
            "total_identified": len(content_to_refresh)
        }

    except Exception as e:
        logger.error(f"Content refresh workflow failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/weekly-report")
async def generate_weekly_report():
    """
    Generate comprehensive weekly master report
    Runs every Monday at 07:00 CET (06:00 UTC)
    """
    try:
        from agents.reporting import report_generator

        result = await report_generator.generate_weekly_master_report()

        if result.get("success"):
            return {
                "success": True,
                "message": "Weekly report generated",
                "report_id": result["report"]["report_id"],
                "summary": result["report"]["summary"]
            }
        else:
            raise HTTPException(status_code=500, detail=result.get("error"))

    except Exception as e:
        logger.error(f"Weekly report generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
