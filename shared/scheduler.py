"""
SAMA 2.0 - Job Scheduler
Runs automated workflows on a schedule using APScheduler.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

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
    "weekly_content_autopilot": {"last_run": None, "last_status": None, "last_error": None},
    "weekly_ai_visibility":   {"last_run": None, "last_status": None, "last_error": None},
    "midday_review_check":    {"last_run": None, "last_status": None, "last_error": None},
    "daily_reflection":       {"last_run": None, "last_status": None, "last_error": None},
    "daily_digest":           {"last_run": None, "last_status": None, "last_error": None},
    "weekly_goal_review":     {"last_run": None, "last_status": None, "last_error": None},
    "daily_dev_health_check": {"last_run": None, "last_status": None, "last_error": None},
    "daily_agent_reports":    {"last_run": None, "last_status": None, "last_error": None},
    "weekly_social_analysis": {"last_run": None, "last_status": None, "last_error": None},
    "daily_lead_scoring":     {"last_run": None, "last_status": None, "last_error": None},
    "weekly_status_email":    {"last_run": None, "last_status": None, "last_error": None},
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
    if job_id not in _job_history:
        _job_history[job_id] = {"last_run": None, "last_status": None, "last_error": None}
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


async def _run_content_autopilot_for_tenant(tenant_id: str, ap_cfg: Dict[str, Any]) -> Dict[str, int]:
    """Run the full content autopilot chain for a single tenant.

    Pipeline:
        1. analyze       — content OODA, auto-feeds gaps into the plan
        2. generate      — AI-batch ideas, deduped against existing plan
        3. draft top N   — materialise top-priority plan items
        4. queue/publish — auto-publish if validation_score passes & enabled,
                           otherwise queue in pending_approvals for the editor
    """
    from api.routes.content_analyze_ooda import run_content_analysis_with_ooda
    from api.routes.content_validation import _heuristic_checks
    from shared.database import get_supabase

    stats = {"plan_items_added": 0, "ideas_generated": 0, "drafted": 0, "queued": 0, "published": 0}
    sb = get_supabase()

    # 1. Analyze (auto-feeds analysis_gap rows into content_plan_items)
    try:
        result = await run_content_analysis_with_ooda(tenant_id=tenant_id)
        stats["plan_items_added"] = int(result.get("plan_items_added") or 0)
    except Exception as e:
        logger.warning(f"[autopilot {tenant_id}] analyze failed: {e}")

    # 2. Generate ideas (batch). Re-implementing the route inline keeps us off
    #    Request-scoped state so we can call it from the scheduler without
    #    fabricating a fake FastAPI Request object.
    try:
        from shared.config import settings as _settings
        import anthropic
        ideas_count = max(1, min(int(ap_cfg.get("ideas_per_run", 6)), 12))

        # Pull brand context the same way the route does.
        brand = {}
        try:
            row = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
            brand = (row.data or {}).get("settings", {}) if row.data else {}
        except Exception:
            brand = {}

        client = anthropic.Anthropic(api_key=_settings.ANTHROPIC_API_KEY)
        prompt = (
            f"You are a B2B SaaS content strategist. Generate {ideas_count} content ideas.\n"
            f"Brand: {brand.get('brand_name','')}\n"
            f"Description: {brand.get('brand_description','')}\n"
            f"Audience: {brand.get('target_audience','')}\n"
            f"Mix: 60% blog_article, 25% linkedin_post, 15% email.\n"
            'Return ONLY a JSON array of objects {title, topic, content_type, target_keyword, pillar, priority, reason}.'
        )
        msg = client.messages.create(model=_settings.CLAUDE_MODEL, max_tokens=2048, messages=[{"role": "user", "content": prompt}])
        import json as _json
        text = msg.content[0].text.strip()
        try:
            ideas = _json.loads(text)
        except Exception:
            ideas = _json.loads(text.split("```")[1].lstrip("json").strip()) if "```" in text else []

        # Dedupe by keyword against existing plan rows.
        existing = sb.table("content_plan_items").select("target_keyword").eq("tenant_id", tenant_id).execute()
        existing_kw = {(r.get("target_keyword") or "").strip().lower() for r in (existing.data or [])}
        rows = []
        for raw in ideas if isinstance(ideas, list) else []:
            kw = str((raw or {}).get("target_keyword") or "").strip()
            if kw and kw.lower() in existing_kw:
                continue
            rows.append({
                "tenant_id": tenant_id,
                "title": str(raw.get("title") or "Untitled idea")[:300],
                "topic": str(raw.get("topic") or "")[:1000],
                "content_type": str(raw.get("content_type") or "blog_article"),
                "target_keyword": kw[:200] or None,
                "pillar": str(raw.get("pillar") or "")[:100],
                "priority": str(raw.get("priority") or "medium"),
                "reason": str(raw.get("reason") or "")[:500],
                "status": "idea",
                "source": "ai_generated",
                "metadata": {"generator": "claude", "model": _settings.CLAUDE_MODEL, "from": "autopilot"},
            })
            existing_kw.add(kw.lower())
        if rows:
            sb.table("content_plan_items").insert(rows).execute()
        stats["ideas_generated"] = len(rows)
    except Exception as e:
        logger.warning(f"[autopilot {tenant_id}] generate failed: {e}")

    # 3. Draft the top-N pending ideas (priority-sorted).
    top_n = max(0, min(int(ap_cfg.get("auto_draft_top_n", 3)), 6))
    if top_n > 0:
        try:
            ideas_q = (
                sb.table("content_plan_items")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("status", "idea")
                .limit(50)
                .execute()
            )
            ideas = ideas_q.data or []
            order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            ideas.sort(key=lambda x: order.get((x.get("priority") or "medium").lower(), 3))
            picked = ideas[:top_n]

            for item in picked:
                try:
                    sb.table("content_plan_items").update({"status": "drafting"}).eq("id", item["id"]).execute()
                    ctype = item.get("content_type") or "blog_article"
                    target_words = "1500-2200 words" if ctype == "blog_article" else "120-180 words" if ctype == "linkedin_post" else "300-500 words"
                    p = (
                        f"Write a {ctype.replace('_',' ')}.\n"
                        f"Title: {item.get('title','')}\nTopic: {item.get('topic','')}\n"
                        f"Target keyword: {item.get('target_keyword','')}\n"
                        f"Pillar: {item.get('pillar','')}\nLength: {target_words}.\n"
                        'Return ONLY a JSON object {title, content, meta_title, meta_description, word_count}.'
                    )
                    m = client.messages.create(model=_settings.CLAUDE_MODEL, max_tokens=4096, messages=[{"role": "user", "content": p}])
                    raw = m.content[0].text.strip()
                    try:
                        article = _json.loads(raw)
                    except Exception:
                        article = _json.loads(raw.split("```")[1].lstrip("json").strip()) if "```" in raw else {"title": item.get("title"), "content": raw}

                    piece_data = {
                        "tenant_id": tenant_id,
                        "title": article.get("title") or item.get("title") or "Untitled",
                        "content": article.get("content") or "",
                        "content_type": ctype,
                        "meta_title": article.get("meta_title") or "",
                        "meta_description": article.get("meta_description") or "",
                        "target_keyword": item.get("target_keyword") or "",
                        "word_count": int(article.get("word_count") or len((article.get("content") or "").split())),
                        "status": "draft",
                    }
                    inserted = sb.table("content_pieces").insert(piece_data).execute()
                    piece = (inserted.data or [{}])[0]
                    piece_id = piece.get("id")
                    sb.table("content_plan_items").update({
                        "status": "draft",
                        "content_piece_id": piece_id,
                    }).eq("id", item["id"]).execute()
                    stats["drafted"] += 1

                    # 4. Decide between auto-publish and queue-for-approval.
                    score = _heuristic_checks(piece_data)["score"]
                    min_score = int(ap_cfg.get("min_score_for_publish", 70))
                    if ap_cfg.get("auto_publish") and score >= min_score:
                        from api.routes.content_validation import _publish_via_github
                        gh = await _publish_via_github(piece_data)
                        if gh.get("success"):
                            sb.table("content_pieces").update({
                                "status": "published",
                                "published_at": datetime.now(timezone.utc).isoformat(),
                                "external_url": gh.get("url"),
                                "target_url": gh.get("url"),
                                "validation_score": score,
                            }).eq("id", piece_id).execute()
                            sb.table("content_plan_items").update({"status": "published"}).eq("content_piece_id", piece_id).execute()
                            stats["published"] += 1
                            continue

                    # Queue for human approval otherwise.
                    sb.table("pending_approvals").insert({
                        "tenant_id": tenant_id,
                        "kind": "content",
                        "channel": ctype,
                        "agent_name": "content_autopilot",
                        "title": piece_data["title"],
                        "body": piece_data["content"][:8000],
                        "metadata": {"piece_id": piece_id, "score": score, "min_score": min_score},
                        "status": "pending",
                    }).execute()
                    stats["queued"] += 1
                except Exception as e:
                    logger.warning(f"[autopilot {tenant_id}] draft failed for {item.get('id')}: {e}")
                    sb.table("content_plan_items").update({"status": "idea"}).eq("id", item["id"]).execute()
        except Exception as e:
            logger.warning(f"[autopilot {tenant_id}] draft phase failed: {e}")

    return stats


async def _run_content_autopilot():
    """Fan out content autopilot to every tenant that has it enabled.

    Reads ``user_settings.settings.content_autopilot``. Tenants with
    ``enabled=true`` get the full chain run for them. Cadence ('weekly' vs
    'biweekly') is honoured by skipping every other Wednesday — we use
    ISO week parity as a cheap deterministic check.
    """
    logger.info("[scheduler] Running content autopilot fan-out...")
    try:
        from shared.database import get_supabase
        sb = get_supabase()
        rows = sb.table("user_settings").select("user_id,settings").execute()
        tenants = []
        for row in (rows.data or []):
            cfg = (row.get("settings") or {}).get("content_autopilot") or {}
            if not cfg.get("enabled"):
                continue
            cadence = (cfg.get("cadence") or "weekly").lower()
            if cadence == "biweekly":
                # Skip on odd ISO weeks so we run every other week.
                if datetime.now(timezone.utc).isocalendar().week % 2 != 0:
                    continue
            tenants.append((row["user_id"], cfg))

        logger.info(f"[scheduler] autopilot: {len(tenants)} tenant(s) opted in")
        totals = {"drafted": 0, "queued": 0, "published": 0}
        for tenant_id, cfg in tenants:
            try:
                stats = await _run_content_autopilot_for_tenant(tenant_id, cfg)
                for k in totals:
                    totals[k] += stats.get(k, 0)
            except Exception as e:
                logger.error(f"[autopilot {tenant_id}] failed: {e}")

        logger.info(f"[scheduler] autopilot done — drafted {totals['drafted']}, queued {totals['queued']}, published {totals['published']}")
        _record("weekly_content_autopilot", "success")
    except Exception as e:
        logger.error(f"[scheduler] content autopilot failed: {e}")
        _record("weekly_content_autopilot", "error", str(e))
        await _notify_failure("weekly_content_autopilot", str(e))


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


async def _run_daily_lead_scoring():
    """Re-score all active leads to catch behavioral changes."""
    try:
        from shared.database import get_supabase
        from shared.lead_scoring import score_lead, check_and_escalate
        sb = get_supabase()
        leads = sb.table("leads").select("id,score").in_("status", ["new", "contacted"]).limit(200).execute()
        updated = 0
        for lead in (leads.data or []):
            new_score = await score_lead(lead["id"])
            if new_score != (lead.get("score") or 0):
                sb.table("leads").update({"score": new_score}).eq("id", lead["id"]).execute()
                await check_and_escalate(lead["id"], new_score)
                updated += 1
        logger.info(f"[scheduler] Lead re-scoring done — {updated}/{len(leads.data or [])} scores updated")
        _record("daily_lead_scoring", "success")
    except Exception as e:
        logger.error(f"[scheduler] Lead scoring failed: {e}")
        _record("daily_lead_scoring", "error", str(e))
        await _notify_failure("daily_lead_scoring", str(e))


async def _run_weekly_status_email():
    """Send the weekly status email to every opted-in user."""
    logger.info("[scheduler] Running weekly status email batch...")
    try:
        from shared.weekly_email import send_weekly_status_for_all
        # send_weekly_status_for_all() does blocking Supabase + Resend calls;
        # run it off the event loop so other scheduled coroutines can progress.
        result = await asyncio.to_thread(send_weekly_status_for_all)
        sent = result.get("sent", 0)
        skipped = result.get("skipped", 0)
        errors = result.get("errors", 0)
        logger.info(
            f"[scheduler] Weekly status email done — sent={sent} skipped={skipped} errors={errors}"
        )
        _record(
            "weekly_status_email",
            "success" if errors == 0 else "partial",
            f"errors={errors}" if errors else None,
        )
    except Exception as e:
        logger.error(f"[scheduler] Weekly status email failed: {e}")
        _record("weekly_status_email", "error", str(e))
        await _notify_failure("weekly_status_email", str(e))


async def _run_for_all_tenants(agent_name: str, schedule: str) -> None:
    """
    Fan-out: for every tenant where (agent_name, schedule) matches the row in
    tenant_agent_config AND enabled is true, dispatch a cycle. Each tenant's
    run is recorded in agent_runs and executes independently in the background
    so one tenant's failure doesn't block the others.
    """
    job_id = f"tenants_{agent_name}_{schedule}"
    logger.info(f"[scheduler] {job_id}: fan-out start")
    try:
        from shared.database import get_supabase
        from api.routes.tenant_activation import _execute_run

        sb = get_supabase()
        rows = (
            sb.table("tenant_agent_config")
            .select("tenant_id")
            .eq("agent_name", agent_name)
            .eq("schedule", schedule)
            .eq("enabled", True)
            .execute()
        )
        tenants = [r["tenant_id"] for r in (rows.data or []) if r.get("tenant_id")]
        logger.info(f"[scheduler] {job_id}: dispatching to {len(tenants)} tenants")

        for tenant_id in tenants:
            run_id = None
            try:
                ins = sb.table("agent_runs").insert({
                    "tenant_id": tenant_id,
                    "agent_name": agent_name,
                    "status": "running",
                }).execute()
                if ins.data:
                    run_id = ins.data[0]["id"]
            except Exception as e:
                logger.warning(f"[scheduler] could not record run for {tenant_id}/{agent_name}: {e}")
            asyncio.create_task(_execute_run(run_id, tenant_id, agent_name))

        _record(job_id, "success")
    except Exception as e:
        logger.error(f"[scheduler] {job_id} failed: {e}")
        _record(job_id, "error", str(e))
        await _notify_failure(job_id, str(e))


async def _run_publish_due_social_posts():
    """Publish any social_posts whose scheduled_for has passed."""
    job_id = "publish_due_social_posts"
    try:
        from datetime import datetime, timezone
        from shared.database import get_supabase
        from api.routes.social_schedule import _publish_to_platform

        sb = get_supabase()
        now_iso = datetime.now(timezone.utc).isoformat()
        result = (
            sb.table("social_posts")
            .select("*")
            .eq("status", "scheduled")
            .lte("scheduled_for", now_iso)
            .limit(50)
            .execute()
        )
        due = result.data or []
        for post in due:
            try:
                delivery = await _publish_to_platform(
                    post["platform"], post["content"], post.get("tenant_id", "default")
                )
                sb.table("social_posts").update(
                    {
                        "status": "published" if delivery["delivered"] else "published_locally",
                        "published_at": now_iso,
                        "engagement_data": {
                            **(post.get("engagement_data") or {}),
                            **delivery,
                        },
                    }
                ).eq("id", post["id"]).execute()
            except Exception as e:
                logger.warning(f"Failed to publish post {post.get('id')}: {e}")
                sb.table("social_posts").update(
                    {"status": "failed", "engagement_data": {"error": str(e)}}
                ).eq("id", post["id"]).execute()
        _record(job_id, "ok")
    except Exception as e:
        logger.error(f"[scheduler] {job_id} failed: {e}")
        _record(job_id, "error", str(e))
        await _notify_failure(job_id, str(e))


async def _run_watchdog() -> None:
    """Mark agent_runs that have been 'running' too long as failed."""
    try:
        from api.routes.tenant_activation import reap_stale_runs
        n = await reap_stale_runs()
        if n:
            logger.info(f"[scheduler] watchdog reaped {n} stale runs")
    except Exception as e:
        logger.warning(f"[scheduler] watchdog failed: {e}")


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

    # Content autopilot — Wednesdays 06:00 UTC (1h after the analysis so its
    # gaps are already in the plan when the autopilot drafts them).
    scheduler.add_job(
        _run_content_autopilot,
        CronTrigger(day_of_week="wed", hour=6, minute=0),
        id="weekly_content_autopilot",
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

    scheduler.add_job(
        _run_daily_lead_scoring,
        CronTrigger(hour=7, minute=0),
        id="daily_lead_scoring",
        replace_existing=True,
    )

    # Weekly status email — Mondays 09:00 UTC.
    # Runs after the weekend so users open the week with a recap of
    # what the agents did and what's pending their approval.
    scheduler.add_job(
        _run_weekly_status_email,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_status_email",
        replace_existing=True,
    )

    # ── Multi-tenant agent fan-out ────────────────────────────────────────
    # Each (agent_name, schedule) combination iterates tenant_agent_config
    # and dispatches a per-tenant cycle. The legacy global jobs above only
    # serve the home brand; these jobs serve every paying tenant.
    tenant_fanout_jobs = [
        ("seo", "daily", CronTrigger(hour=2, minute=30)),
        ("analytics", "daily", CronTrigger(hour=4, minute=30)),
        ("social", "daily", CronTrigger(hour=6, minute=30)),
        ("reviews", "daily", CronTrigger(hour=14, minute=30)),
        ("content", "weekly", CronTrigger(day_of_week="wed", hour=5, minute=30)),
        ("geo", "weekly", CronTrigger(day_of_week="thu", hour=10, minute=30)),
        # Strategy runs Sunday 18:00 UTC, after a full week of domain activity.
        ("strategy", "weekly", CronTrigger(day_of_week="sun", hour=18, minute=0)),
    ]
    for agent_name, schedule_kind, trigger in tenant_fanout_jobs:
        job_id = f"tenants_{agent_name}_{schedule_kind}"
        scheduler.add_job(
            _run_for_all_tenants,
            trigger,
            args=[agent_name, schedule_kind],
            id=job_id,
            replace_existing=True,
        )

    # Watchdog: reap orphaned "running" rows every 5 minutes so a process
    # restart mid-cycle doesn't leave the dashboard stuck on a spinner.
    scheduler.add_job(
        _run_watchdog,
        CronTrigger(minute="*/5"),
        id="agent_runs_watchdog",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "[scheduler] Started — "
        "keywords 02:00, SEO OODA Mon 03:00, metrics 04:00, "
        "agent-reports 05:00, dev-health 05:30, workflow 06:00, ads OODA 08:00, "
        "weekly-email Mon 09:00, social OODA Tue 11:00, "
        "reviews OODA 14:00, content OODA Wed 05:00, content autopilot Wed 06:00, "
        "digest 17:00, reflection 22:00, goals Fri 09:00 (UTC)"
    )


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped")
