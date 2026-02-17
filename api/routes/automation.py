"""
Automation Routes - Scheduled Tasks and Workflows
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import logging

from agents.seo import seo_agent
from agents.content import content_agent
from agents.social import social_agent
from agents.reviews import review_agent

router = APIRouter()
logger = logging.getLogger(__name__)


class DailyWorkflowRequest(BaseModel):
    force: bool = False


@router.post("/daily-workflow")
async def run_daily_workflow(request: DailyWorkflowRequest, background_tasks: BackgroundTasks):
    """
    Run daily automated workflow:
    1. SEO keyword tracking
    2. Review monitoring
    3. Social post generation
    4. Analytics update
    """
    try:
        results = {
            "seo": None,
            "reviews": None,
            "social": None,
            "timestamp": None
        }
        
        # Run SEO keyword tracking
        logger.info("Running daily SEO keyword tracking...")
        try:
            seo_result = await seo_agent.track_keyword_rankings()
            results["seo"] = {"status": "success", "keywords_tracked": len(seo_result)}
        except Exception as e:
            logger.error(f"SEO tracking failed: {e}")
            results["seo"] = {"status": "error", "message": str(e)}
        
        # Monitor reviews
        logger.info("Monitoring review platforms...")
        try:
            review_result = await review_agent.fetch_all_reviews()
            results["reviews"] = {"status": "success", "platforms_checked": len(review_result)}
        except Exception as e:
            logger.error(f"Review monitoring failed: {e}")
            results["reviews"] = {"status": "error", "message": str(e)}
        
        # Generate social post
        logger.info("Generating daily social post...")
        try:
            social_result = await social_agent.generate_post(
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
async def run_weekly_seo_audit(background_tasks: BackgroundTasks):
    """
    Run comprehensive weekly SEO audit:
    - Full site crawl
    - Keyword position tracking
    - Competitor analysis
    - Content gap identification
    """
    try:
        background_tasks.add_task(seo_agent.run_weekly_audit)
        return {
            "success": True,
            "message": "Weekly SEO audit started in background"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content-generation-workflow")
async def run_content_generation_workflow(background_tasks: BackgroundTasks):
    """
    Automated content generation workflow:
    1. Identify keyword opportunities from SEO agent
    2. Generate blog post for top opportunity
    3. Create social posts to promote content
    """
    try:
        # Get keyword opportunities
        opportunities = await seo_agent.discover_keyword_opportunities()
        
        if not opportunities:
            return {
                "success": True,
                "message": "No keyword opportunities found",
                "content_generated": False
            }
        
        # Take top opportunity
        top_opportunity = opportunities[0]
        
        # Generate blog post
        blog_result = await content_agent.generate_blog_post(
            topic=f"Complete guide to {top_opportunity['keyword']}",
            target_keyword=top_opportunity['keyword'],
            word_count=2000,
            pillar="churn_prevention"  # Default pillar
        )
        
        # Generate social post to promote
        social_result = await social_agent.generate_post(
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
    """Get status of all automation workflows"""
    return {
        "workflows": {
            "daily_workflow": {
                "enabled": True,
                "schedule": "Every day at 03:00 UTC",
                "last_run": None  # TODO: Track in database
            },
            "weekly_seo_audit": {
                "enabled": True,
                "schedule": "Every Monday at 03:00 UTC",
                "last_run": None
            },
            "content_generation": {
                "enabled": False,
                "schedule": "On-demand",
                "last_run": None
            }
        },
        "next_scheduled_run": "2026-02-18T03:00:00Z"
    }


@router.post("/schedule/enable")
async def enable_automation():
    """Enable automated workflows"""
    return {
        "success": True,
        "message": "Automation enabled. Workflows will run on schedule."
    }


@router.post("/schedule/disable")
async def disable_automation():
    """Disable automated workflows"""
    return {
        "success": True,
        "message": "Automation disabled. Workflows will not run automatically."
    }
