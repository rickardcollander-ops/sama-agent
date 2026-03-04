"""
SAMA 2.0 - Job Scheduler
Runs automated workflows on a schedule using APScheduler.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# In-memory tracking of job runs
_job_history: Dict[str, Dict[str, Any]] = {
    "daily_keyword_tracking": {"last_run": None, "last_status": None, "last_error": None},
    "weekly_seo_audit":       {"last_run": None, "last_status": None, "last_error": None},
    "daily_workflow":         {"last_run": None, "last_status": None, "last_error": None},
}

scheduler = AsyncIOScheduler(timezone="UTC")


def get_job_history() -> Dict[str, Dict[str, Any]]:
    return _job_history


def _record(job_id: str, status: str, error: Optional[str] = None):
    _job_history[job_id]["last_run"] = datetime.now(timezone.utc).isoformat()
    _job_history[job_id]["last_status"] = status
    _job_history[job_id]["last_error"] = error


async def _run_daily_keyword_tracking():
    """Fetch fresh GSC ranking data for all tracked keywords."""
    logger.info("[scheduler] Running daily keyword tracking...")
    try:
        from agents.seo import seo_agent
        result = await seo_agent.track_keyword_rankings()
        tracked = len(result) if isinstance(result, list) else result.get("keywords_updated", 0)
        logger.info(f"[scheduler] Keyword tracking done — {tracked} keywords updated")
        _record("daily_keyword_tracking", "success")
    except Exception as e:
        logger.error(f"[scheduler] Keyword tracking failed: {e}")
        _record("daily_keyword_tracking", "error", str(e))


async def _run_weekly_seo_audit():
    """Run full SEO audit — technical checks + GSC summary + Claude recommendations."""
    logger.info("[scheduler] Running weekly SEO audit...")
    try:
        from agents.seo import seo_agent
        await seo_agent.run_weekly_audit()
        logger.info("[scheduler] Weekly SEO audit done")
        _record("weekly_seo_audit", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly SEO audit failed: {e}")
        _record("weekly_seo_audit", "error", str(e))


async def _run_daily_workflow():
    """
    Daily workflow:
    1. Monitor reviews
    2. Generate a social post
    """
    logger.info("[scheduler] Running daily workflow...")
    errors = []

    try:
        from agents.reviews import review_agent
        await review_agent.fetch_all_reviews()
        logger.info("[scheduler] Review monitoring done")
    except Exception as e:
        logger.error(f"[scheduler] Review monitoring failed: {e}")
        errors.append(str(e))

    try:
        from agents.social import social_agent
        await social_agent.generate_post(
            topic="Daily CS insight or product update",
            style="educational"
        )
        logger.info("[scheduler] Social post generated")
    except Exception as e:
        logger.error(f"[scheduler] Social post generation failed: {e}")
        errors.append(str(e))

    status = "error" if errors else "success"
    _record("daily_workflow", status, "; ".join(errors) if errors else None)
    logger.info(f"[scheduler] Daily workflow finished with status: {status}")


def start():
    """Register all jobs and start the scheduler."""
    # Daily keyword tracking — 02:00 UTC every day
    scheduler.add_job(
        _run_daily_keyword_tracking,
        CronTrigger(hour=2, minute=0),
        id="daily_keyword_tracking",
        replace_existing=True,
    )

    # Weekly SEO audit — Mondays 03:00 UTC
    scheduler.add_job(
        _run_weekly_seo_audit,
        CronTrigger(day_of_week="mon", hour=3, minute=0),
        id="weekly_seo_audit",
        replace_existing=True,
    )

    # Daily workflow (reviews + social) — 06:00 UTC every day
    scheduler.add_job(
        _run_daily_workflow,
        CronTrigger(hour=6, minute=0),
        id="daily_workflow",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[scheduler] Started — keyword tracking 02:00, SEO audit Mondays 03:00, daily workflow 06:00 (UTC)")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped")
