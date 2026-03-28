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


async def _notify_failure(job_id: str, error: str):
    """Send a dashboard notification when a scheduled job fails."""
    try:
        from shared.notifications import notification_service
        await notification_service.notify(
            title=f"Scheduled job failed: {job_id}",
            message=error[:200],
            severity="warning",
            agent="scheduler",
        )
    except Exception:
        pass  # notification table may not exist

# In-memory tracking of job runs
_job_history: Dict[str, Dict[str, Any]] = {
    "daily_keyword_tracking": {"last_run": None, "last_status": None, "last_error": None},
    "weekly_seo_audit":       {"last_run": None, "last_status": None, "last_error": None},
    "daily_workflow":         {"last_run": None, "last_status": None, "last_error": None},
    "daily_metrics":          {"last_run": None, "last_status": None, "last_error": None},
    "daily_ads_check":        {"last_run": None, "last_status": None, "last_error": None},
    "weekly_content_analysis": {"last_run": None, "last_status": None, "last_error": None},
    "weekly_ai_visibility":   {"last_run": None, "last_status": None, "last_error": None},
    "midday_review_check":    {"last_run": None, "last_status": None, "last_error": None},
    "daily_reflection":       {"last_run": None, "last_status": None, "last_error": None},
    "daily_digest":           {"last_run": None, "last_status": None, "last_error": None},
    "weekly_goal_review":     {"last_run": None, "last_status": None, "last_error": None},
    "daily_dev_health_check": {"last_run": None, "last_status": None, "last_error": None},
    "daily_agent_reports":    {"last_run": None, "last_status": None, "last_error": None},
    "weekly_social_analysis": {"last_run": None, "last_status": None, "last_error": None},
}

scheduler = AsyncIOScheduler(timezone="UTC")


def get_job_history() -> Dict[str, Dict[str, Any]]:
    """Return job history enriched with next_run from APScheduler."""
    result = {}
    for job_id, info in _job_history.items():
        entry = dict(info)
        # Add next_run from APScheduler if available
        try:
            job = scheduler.get_job(job_id)
            if job and job.next_run_time:
                entry["next_run"] = job.next_run_time.isoformat()
            else:
                entry["next_run"] = None
        except Exception:
            entry["next_run"] = None
        result[job_id] = entry
    return result


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
        await _notify_failure("daily_keyword_tracking", str(e))


async def _run_weekly_seo_audit():
    """Run full SEO OODA cycle — observe data, orient analysis, decide actions."""
    logger.info("[scheduler] Running weekly SEO OODA analysis...")
    try:
        from api.routes.seo_analyze_ooda import run_seo_analysis_with_ooda
        result = await run_seo_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] SEO OODA done — {total} actions generated")
        _record("weekly_seo_audit", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly SEO OODA failed: {e}")
        _record("weekly_seo_audit", "error", str(e))
        await _notify_failure("weekly_seo_audit", str(e))


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
    if errors:
        await _notify_failure("daily_workflow", "; ".join(errors))
    logger.info(f"[scheduler] Daily workflow finished with status: {status}")


async def _run_daily_metrics():
    """Collect daily metrics from all channels into the daily_metrics table."""
    logger.info("[scheduler] Running daily metrics collection...")
    try:
        from agents.analytics import analytics_agent
        result = await analytics_agent.collect_daily_metrics()
        channels = result.get("total_channels", 0)
        logger.info(f"[scheduler] Daily metrics collected — {channels} channels")
        _record("daily_metrics", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily metrics collection failed: {e}")
        _record("daily_metrics", "error", str(e))
        await _notify_failure("daily_metrics", str(e))


async def _run_daily_ads_check():
    """Run ads OODA cycle — observe campaigns, orient analysis, decide actions."""
    logger.info("[scheduler] Running daily ads OODA analysis...")
    try:
        from api.routes.ads_analyze_ooda import run_ads_analysis_with_ooda
        result = await run_ads_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Ads OODA done — {total} actions generated")
        _record("daily_ads_check", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily ads OODA failed: {e}")
        _record("daily_ads_check", "error", str(e))
        await _notify_failure("daily_ads_check", str(e))


async def _run_weekly_content_analysis():
    """Run content OODA cycle — observe gaps, orient analysis, decide actions."""
    logger.info("[scheduler] Running weekly content OODA analysis...")
    try:
        from api.routes.content_analyze_ooda import run_content_analysis_with_ooda
        result = await run_content_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Content OODA done — {total} actions generated")
        _record("weekly_content_analysis", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly content OODA failed: {e}")
        _record("weekly_content_analysis", "error", str(e))
        await _notify_failure("weekly_content_analysis", str(e))


async def _run_weekly_ai_visibility():
    """Check AI visibility across ChatGPT, Claude, Gemini, Perplexity."""
    logger.info("[scheduler] Running weekly AI visibility check...")
    try:
        from agents.ai_visibility import ai_visibility_agent
        result = await ai_visibility_agent.check_visibility()
        score = result.get("overall_score", 0) if isinstance(result, dict) else 0
        logger.info(f"[scheduler] AI visibility check done — score: {score}")
        _record("weekly_ai_visibility", "success")
    except Exception as e:
        logger.error(f"[scheduler] AI visibility check failed: {e}")
        _record("weekly_ai_visibility", "error", str(e))
        await _notify_failure("weekly_ai_visibility", str(e))


async def _run_midday_review_check():
    """Run reviews OODA cycle — fetch reviews, analyze sentiment, decide responses."""
    logger.info("[scheduler] Running midday reviews OODA analysis...")
    try:
        from api.routes.reviews_analyze_ooda import run_reviews_analysis_with_ooda
        result = await run_reviews_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Reviews OODA done — {total} actions generated")
        _record("midday_review_check", "success")
    except Exception as e:
        logger.error(f"[scheduler] Reviews OODA failed: {e}")
        _record("midday_review_check", "error", str(e))
        await _notify_failure("midday_review_check", str(e))


async def _run_weekly_social_analysis():
    """Run social OODA cycle — observe engagement, orient trends, decide posts."""
    logger.info("[scheduler] Running weekly social OODA analysis...")
    try:
        from api.routes.social_analyze_ooda import run_social_analysis_with_ooda
        result = await run_social_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Social OODA done — {total} actions generated")
        _record("weekly_social_analysis", "success")
    except Exception as e:
        logger.error(f"[scheduler] Social OODA failed: {e}")
        _record("weekly_social_analysis", "error", str(e))
        await _notify_failure("weekly_social_analysis", str(e))


async def _run_daily_reflection():
    """Run reflection on completed actions across all agents."""
    logger.info("[scheduler] Running daily reflection...")
    try:
        from shared.memory import AgentMemory
        total = 0
        for agent_name in ["seo", "content", "ads", "social", "reviews", "analytics"]:
            memory = AgentMemory(agent_name)
            count = await memory.run_reflection_for_completed_actions()
            total += count
        logger.info(f"[scheduler] Reflection done — {total} actions reflected on")
        _record("daily_reflection", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily reflection failed: {e}")
        _record("daily_reflection", "error", str(e))
        await _notify_failure("daily_reflection", str(e))


async def _run_daily_digest():
    """Send daily activity digest notification."""
    logger.info("[scheduler] Running daily digest...")
    try:
        from shared.notifications import notification_service
        from shared.database import get_supabase
        sb = get_supabase()

        # Count today's activity
        from datetime import datetime, timedelta
        today = (datetime.utcnow() - timedelta(hours=24)).isoformat()

        executed = sb.table("agent_actions") \
            .select("id", count="exact") \
            .in_("status", ["completed", "auto_executed"]) \
            .gte("executed_at", today) \
            .limit(0).execute()

        pending = sb.table("agent_actions") \
            .select("id", count="exact") \
            .eq("status", "pending") \
            .limit(0).execute()

        summary = {
            "actions_executed": executed.count or 0,
            "pending_actions": pending.count or 0,
            "alerts": 0,
            "wins": [],
        }
        await notification_service.send_daily_digest(summary)
        logger.info("[scheduler] Daily digest sent")
        _record("daily_digest", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily digest failed: {e}")
        _record("daily_digest", "error", str(e))
        await _notify_failure("daily_digest", str(e))


async def _run_daily_agent_reports():
    """Generate daily self-reports for all agents."""
    logger.info("[scheduler] Running daily agent reports...")
    try:
        from shared.agent_report import generate_all_reports
        reports = await generate_all_reports()
        logger.info(f"[scheduler] Agent reports done — {len(reports)} reports generated")
        _record("daily_agent_reports", "success")
    except Exception as e:
        logger.error(f"[scheduler] Agent reports failed: {e}")
        _record("daily_agent_reports", "error", str(e))
        await _notify_failure("daily_agent_reports", str(e))


async def _run_daily_dev_health_check():
    """Run the dev agent's full system health check."""
    logger.info("[scheduler] Running daily dev health check...")
    try:
        from agents.dev_agent import dev_agent
        report = await dev_agent.run_full_health_check()
        await dev_agent.save_report(report)
        status = report["summary"]["status"]
        pct = report["summary"]["health_pct"]
        logger.info(f"[scheduler] Dev health check done — {pct}% healthy ({status})")
        _record("daily_dev_health_check", "success")
    except Exception as e:
        logger.error(f"[scheduler] Dev health check failed: {e}")
        _record("daily_dev_health_check", "error", str(e))
        await _notify_failure("daily_dev_health_check", str(e))


async def _run_weekly_goal_review():
    """Review progress on all active goals."""
    logger.info("[scheduler] Running weekly goal review...")
    try:
        from shared.goals import goal_tracker
        goals = await goal_tracker.get_active_goals()
        for goal in goals:
            status = await goal_tracker.check_goal_status(goal)
            logger.info(f"[scheduler] Goal '{goal.get('goal_text', '')[:40]}': {status}")
        logger.info(f"[scheduler] Goal review done — {len(goals)} goals reviewed")
        _record("weekly_goal_review", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly goal review failed: {e}")
        _record("weekly_goal_review", "error", str(e))
        await _notify_failure("weekly_goal_review", str(e))


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

    # Daily metrics collection — 04:00 UTC every day (after keyword tracking)
    scheduler.add_job(
        _run_daily_metrics,
        CronTrigger(hour=4, minute=0),
        id="daily_metrics",
        replace_existing=True,
    )

    # Daily ads check — 08:00 UTC every day
    scheduler.add_job(
        _run_daily_ads_check,
        CronTrigger(hour=8, minute=0),
        id="daily_ads_check",
        replace_existing=True,
    )

    # Weekly content gap analysis — Wednesdays 05:00 UTC
    scheduler.add_job(
        _run_weekly_content_analysis,
        CronTrigger(day_of_week="wed", hour=5, minute=0),
        id="weekly_content_analysis",
        replace_existing=True,
    )

    # Weekly AI visibility check — Thursdays 10:00 UTC
    scheduler.add_job(
        _run_weekly_ai_visibility,
        CronTrigger(day_of_week="thu", hour=10, minute=0),
        id="weekly_ai_visibility",
        replace_existing=True,
    )

    # Midday review check — 14:00 UTC every day
    scheduler.add_job(
        _run_midday_review_check,
        CronTrigger(hour=14, minute=0),
        id="midday_review_check",
        replace_existing=True,
    )

    # Daily reflection — 22:00 UTC (review completed actions)
    scheduler.add_job(
        _run_daily_reflection,
        CronTrigger(hour=22, minute=0),
        id="daily_reflection",
        replace_existing=True,
    )

    # Daily digest notification — 17:00 UTC
    scheduler.add_job(
        _run_daily_digest,
        CronTrigger(hour=17, minute=0),
        id="daily_digest",
        replace_existing=True,
    )

    # Weekly goal review — Fridays 09:00 UTC
    scheduler.add_job(
        _run_weekly_goal_review,
        CronTrigger(day_of_week="fri", hour=9, minute=0),
        id="weekly_goal_review",
        replace_existing=True,
    )

    # Daily agent self-reports — 05:00 UTC (before dev health check)
    scheduler.add_job(
        _run_daily_agent_reports,
        CronTrigger(hour=5, minute=0),
        id="daily_agent_reports",
        replace_existing=True,
    )

    # Daily dev health check — 05:30 UTC (after reports, before main agent jobs)
    scheduler.add_job(
        _run_daily_dev_health_check,
        CronTrigger(hour=5, minute=30),
        id="daily_dev_health_check",
        replace_existing=True,
    )

    # Weekly social analysis — Tuesdays 11:00 UTC
    scheduler.add_job(
        _run_weekly_social_analysis,
        CronTrigger(day_of_week="tue", hour=11, minute=0),
        id="weekly_social_analysis",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "[scheduler] Started — "
        "keywords 02:00, SEO OODA Mon 03:00, metrics 04:00, "
        "agent-reports 05:00, dev-health 05:30, workflow 06:00, ads OODA 08:00, "
        "social OODA Tue 11:00, AI visibility Thu 10:00, "
        "reviews OODA 14:00, content OODA Wed 05:00, "
        "digest 17:00, reflection 22:00, goals Fri 09:00 (UTC)"
    )


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped")
