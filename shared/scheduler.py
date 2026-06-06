"""
SAMA 2.0 - Job Scheduler
Runs automated workflows on a schedule using APScheduler.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

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
    "hourly_due_content_drafts": {"last_run": None, "last_status": None, "last_error": None},
    "hourly_social_posts_email": {"last_run": None, "last_status": None, "last_error": None},
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
    "daily_content_refresh":  {"last_run": None, "last_status": None, "last_error": None},
}

scheduler = AsyncIOScheduler(timezone="UTC")


# Map kind → scheduler job_id for the email jobs that the admin UI controls.
# Values are (job_id, default_dow, default_hour, default_minute).
_EMAIL_JOBS: Dict[str, Tuple[str, Optional[str], Optional[int], int]] = {
    "weekly_status": ("weekly_status_email",       "mon", 9,    0),
    "social_posts":  ("hourly_social_posts_email", None,  None, 15),
}

# Cache of the last-applied email_schedules row per kind. Used by
# `_run_reload_email_schedules` to detect changes and reschedule the
# corresponding job without restarting the process.
_email_schedule_state: Dict[str, Dict[str, Any]] = {}


def _read_email_schedule(kind: str) -> Optional[Dict[str, Any]]:
    """Return the email_schedules row for `kind`, or None on lookup failure.

    Failure is silent on purpose — if the migration hasn't been applied yet
    the scheduler should still come up using the hard-coded defaults.
    """
    try:
        from shared.database import get_supabase
        sb = get_supabase()
        result = (
            sb.table("email_schedules")
            .select("kind,enabled,cron_day_of_week,cron_hour,cron_minute,timezone,updated_at")
            .eq("kind", kind)
            .single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.debug(f"[scheduler] email_schedules lookup failed for {kind}: {e}")
        return None


def _build_email_trigger(kind: str) -> CronTrigger:
    """Build a CronTrigger for an email job from email_schedules + defaults.

    Falls back to the per-kind defaults baked into _EMAIL_JOBS when the row
    is missing or partial. Caches the row in `_email_schedule_state` so the
    reload loop can diff against it.
    """
    _, default_dow, default_hour, default_minute = _EMAIL_JOBS[kind]
    row = _read_email_schedule(kind) or {}
    _email_schedule_state[kind] = row

    dow = row.get("cron_day_of_week") if row else default_dow
    hour = row.get("cron_hour") if row else default_hour
    minute = row.get("cron_minute") if row else default_minute
    if minute is None:
        minute = default_minute
    tz = row.get("timezone") or "UTC"

    kwargs: Dict[str, Any] = {"minute": int(minute), "timezone": tz}
    if hour is not None:
        kwargs["hour"] = int(hour)
    if dow:
        kwargs["day_of_week"] = dow
    return CronTrigger(**kwargs)


def _email_schedule_enabled(kind: str) -> bool:
    """Return whether the kind is currently enabled. Defaults to True if
    the row is missing so a fresh install keeps sending."""
    row = _email_schedule_state.get(kind)
    if row is None:
        row = _read_email_schedule(kind)
        _email_schedule_state[kind] = row or {}
    if not row:
        return True
    enabled = row.get("enabled")
    return bool(enabled) if enabled is not None else True


def get_job_history() -> Dict[str, Dict[str, Any]]:
    """Return job history enriched with next_run from APScheduler."""
    result = {}
    for job_id, info in _job_history.items():
        entry = dict(info)
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
    logger.info("[scheduler] Running daily keyword tracking...")
    try:
        from agents.seo import seo_agent
        result = await seo_agent.track_keyword_rankings()
        tracked = len(result) if isinstance(result, list) else result.get("keywords_updated", 0)
        logger.info(f"[scheduler] Keyword tracking done -- {tracked} keywords updated")
        _record("daily_keyword_tracking", "success")
    except Exception as e:
        logger.error(f"[scheduler] Keyword tracking failed: {e}")
        _record("daily_keyword_tracking", "error", str(e))
        await _notify_failure("daily_keyword_tracking", str(e))


async def _run_weekly_seo_audit():
    logger.info("[scheduler] Running weekly SEO OODA analysis...")
    try:
        from api.routes.seo_analyze_ooda import run_seo_analysis_with_ooda
        result = await run_seo_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] SEO OODA done -- {total} actions generated")
        _record("weekly_seo_audit", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly SEO OODA failed: {e}")
        _record("weekly_seo_audit", "error", str(e))
        await _notify_failure("weekly_seo_audit", str(e))


async def _run_daily_workflow():
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
    logger.info("[scheduler] Running daily metrics collection...")
    try:
        from agents.analytics import analytics_agent
        result = await analytics_agent.collect_daily_metrics()
        channels = result.get("total_channels", 0)
        logger.info(f"[scheduler] Daily metrics collected -- {channels} channels")
        _record("daily_metrics", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily metrics collection failed: {e}")
        _record("daily_metrics", "error", str(e))
        await _notify_failure("daily_metrics", str(e))


async def _run_daily_ads_check():
    logger.info("[scheduler] Running daily ads OODA analysis...")
    try:
        from api.routes.ads_analyze_ooda import run_ads_analysis_with_ooda
        result = await run_ads_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Ads OODA done -- {total} actions generated")
        _record("daily_ads_check", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily ads OODA failed: {e}")
        _record("daily_ads_check", "error", str(e))
        await _notify_failure("daily_ads_check", str(e))


async def _run_weekly_content_analysis():
    logger.info("[scheduler] Running weekly content OODA analysis...")
    try:
        from api.routes.content_analyze_ooda import run_content_analysis_with_ooda
        result = await run_content_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Content OODA done -- {total} actions generated")
        _record("weekly_content_analysis", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly content OODA failed: {e}")
        _record("weekly_content_analysis", "error", str(e))
        await _notify_failure("weekly_content_analysis", str(e))


async def _run_content_autopilot_for_tenant(tenant_id: str, ap_cfg: Dict[str, Any]) -> Dict[str, int]:
    from api.routes.content_analyze_ooda import run_content_analysis_with_ooda
    from api.routes.content_validation import _heuristic_checks
    from shared.database import get_supabase

    stats = {"plan_items_added": 0, "ideas_generated": 0, "drafted": 0, "queued": 0, "published": 0}
    sb = get_supabase()

    # Resolve the scheduled target date (today + N days) once. We pin the local
    # time-of-day to 09:00 Europe/Stockholm and store the instant as UTC, so the
    # hourly UTC publish job fires on the correct local calendar day (and stays
    # inside that local day regardless of DST).
    _STO = ZoneInfo("Europe/Stockholm")
    days_ahead = ap_cfg.get("scheduled_for_days_ahead")
    target_dt_iso: Optional[str] = None
    target_date = None
    if days_ahead is not None:
        local = (datetime.now(_STO) + timedelta(days=int(days_ahead))).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        target_dt_iso = local.astimezone(timezone.utc).isoformat()
        target_date = local.date()

    # Gap-fill, not blind generation: if the daily cron has already filled the
    # target date, skip this run entirely.
    if ap_cfg.get("source") == "daily_cron" and target_date is not None:
        try:
            day_start = datetime(
                target_date.year, target_date.month, target_date.day, tzinfo=_STO
            ).astimezone(timezone.utc)
            day_end = day_start + timedelta(days=1)
            existing = (
                sb.table("content_plan_items")
                .select("id")
                .eq("tenant_id", tenant_id)
                .gte("scheduled_for", day_start.isoformat())
                .lt("scheduled_for", day_end.isoformat())
                .in_("status", ["idea", "drafting", "draft", "scheduled", "published"])
                .limit(1)
                .execute()
            )
            if existing.data:
                logger.info(
                    f"[autopilot {tenant_id}] skip: already scheduled for {target_date.isoformat()}"
                )
                return {**stats, "skipped": True, "reason": "already_scheduled_for_target_date"}
        except Exception as e:
            logger.warning(f"[autopilot {tenant_id}] gap-fill check failed: {e}")

    try:
        result = await run_content_analysis_with_ooda(tenant_id=tenant_id)
        stats["plan_items_added"] = int(result.get("plan_items_added") or 0)
    except Exception as e:
        logger.warning(f"[autopilot {tenant_id}] analyze failed: {e}")

    try:
        from shared.config import settings as _settings
        import anthropic
        ideas_count = max(1, min(int(ap_cfg.get("ideas_per_run", 6)), 12))

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
                    plan_update = {"status": "draft", "content_piece_id": piece_id}
                    # Pin the calendar date. Only opt into scheduled auto-publish
                    # (process_due_scheduled_items ships it unattended on day +N)
                    # when the user actually enabled auto_publish. With
                    # auto_publish off — the default for daily/weekly cron — the
                    # piece is scheduled on the calendar but still needs manual
                    # approval before it goes live.
                    auto_pub = bool(ap_cfg.get("auto_publish"))
                    if target_dt_iso:
                        plan_update["scheduled_for"] = target_dt_iso
                        plan_update["auto_publish_on_schedule"] = auto_pub
                    sb.table("content_plan_items").update(plan_update).eq("id", item["id"]).execute()
                    stats["drafted"] += 1

                    score = _heuristic_checks(piece_data)["score"]
                    min_score = int(ap_cfg.get("min_score_for_publish", 70))
                    if auto_pub and score >= min_score:
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

                    # When auto_publish is on, the scheduled date IS the approval
                    # mechanism — the hourly job publishes it on day +N, so don't
                    # also queue it for manual review (avoids a confusing double
                    # state). With auto_publish off the schedule will NOT publish
                    # it, so it must fall through to the approval queue below.
                    if target_dt_iso and auto_pub:
                        stats["scheduled"] = stats.get("scheduled", 0) + 1
                        continue

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

    if target_dt_iso:
        stats["scheduled_for"] = target_dt_iso
    return stats


async def _run_content_autopilot():
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

        logger.info(f"[scheduler] autopilot done -- drafted {totals['drafted']}, queued {totals['queued']}, published {totals['published']}")
        _record("weekly_content_autopilot", "success")
    except Exception as e:
        logger.error(f"[scheduler] content autopilot failed: {e}")
        _record("weekly_content_autopilot", "error", str(e))
        await _notify_failure("weekly_content_autopilot", str(e))


async def _run_weekly_ai_visibility():
    logger.info("[scheduler] Running weekly AI visibility check...")
    try:
        from agents.ai_visibility import ai_visibility_agent
        result = await ai_visibility_agent.check_visibility()
        score = result.get("overall_score", 0) if isinstance(result, dict) else 0
        logger.info(f"[scheduler] AI visibility check done -- score: {score}")
        _record("weekly_ai_visibility", "success")
    except Exception as e:
        logger.error(f"[scheduler] AI visibility check failed: {e}")
        _record("weekly_ai_visibility", "error", str(e))
        await _notify_failure("weekly_ai_visibility", str(e))


async def _run_midday_review_check():
    logger.info("[scheduler] Running midday reviews OODA analysis...")
    try:
        from api.routes.reviews_analyze_ooda import run_reviews_analysis_with_ooda
        result = await run_reviews_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Reviews OODA done -- {total} actions generated")
        _record("midday_review_check", "success")
    except Exception as e:
        logger.error(f"[scheduler] Reviews OODA failed: {e}")
        _record("midday_review_check", "error", str(e))
        await _notify_failure("midday_review_check", str(e))


async def _run_weekly_social_analysis():
    logger.info("[scheduler] Running weekly social OODA analysis...")
    try:
        from api.routes.social_analyze_ooda import run_social_analysis_with_ooda
        result = await run_social_analysis_with_ooda()
        total = result.get("summary", {}).get("total_actions", 0)
        logger.info(f"[scheduler] Social OODA done -- {total} actions generated")
        _record("weekly_social_analysis", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly social OODA failed: {e}")
        _record("weekly_social_analysis", "error", str(e))
        await _notify_failure("weekly_social_analysis", str(e))


async def _run_daily_reflection():
    logger.info("[scheduler] Running daily reflection...")
    try:
        from shared.memory import AgentMemory
        total = 0
        for agent_name in ["seo", "content", "ads", "social", "reviews", "analytics"]:
            memory = AgentMemory(agent_name)
            count = await memory.run_reflection_for_completed_actions()
            total += count
        logger.info(f"[scheduler] Reflection done -- {total} actions reflected on")
        _record("daily_reflection", "success")
    except Exception as e:
        logger.error(f"[scheduler] Daily reflection failed: {e}")
        _record("daily_reflection", "error", str(e))
        await _notify_failure("daily_reflection", str(e))


async def _run_daily_digest():
    logger.info("[scheduler] Running daily digest...")
    try:
        from shared.notifications import notification_service
        from shared.database import get_supabase
        sb = get_supabase()

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
    logger.info("[scheduler] Running daily agent reports...")
    try:
        from shared.agent_report import generate_all_reports
        reports = await generate_all_reports()
        logger.info(f"[scheduler] Agent reports done -- {len(reports)} reports generated")
        _record("daily_agent_reports", "success")
    except Exception as e:
        logger.error(f"[scheduler] Agent reports failed: {e}")
        _record("daily_agent_reports", "error", str(e))
        await _notify_failure("daily_agent_reports", str(e))


async def _run_daily_dev_health_check():
    logger.info("[scheduler] Running daily dev health check...")
    try:
        from agents.dev_agent import dev_agent
        report = await dev_agent.run_full_health_check()
        await dev_agent.save_report(report)
        status = report["summary"]["status"]
        pct = report["summary"]["health_pct"]
        logger.info(f"[scheduler] Dev health check done -- {pct}% healthy ({status})")
        _record("daily_dev_health_check", "success")
    except Exception as e:
        logger.error(f"[scheduler] Dev health check failed: {e}")
        _record("daily_dev_health_check", "error", str(e))
        await _notify_failure("daily_dev_health_check", str(e))


async def _run_weekly_goal_review():
    logger.info("[scheduler] Running weekly goal review...")
    try:
        from shared.goals import goal_tracker
        goals = await goal_tracker.get_active_goals()
        for goal in goals:
            status = await goal_tracker.check_goal_status(goal)
            logger.info(f"[scheduler] Goal '{goal.get('goal_text', '')[:40]}': {status}")
        logger.info(f"[scheduler] Goal review done -- {len(goals)} goals reviewed")
        _record("weekly_goal_review", "success")
    except Exception as e:
        logger.error(f"[scheduler] Weekly goal review failed: {e}")
        _record("weekly_goal_review", "error", str(e))
        await _notify_failure("weekly_goal_review", str(e))


async def _run_daily_lead_scoring():
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
        logger.info(f"[scheduler] Lead re-scoring done -- {updated}/{len(leads.data or [])} scores updated")
        _record("daily_lead_scoring", "success")
    except Exception as e:
        logger.error(f"[scheduler] Lead scoring failed: {e}")
        _record("daily_lead_scoring", "error", str(e))
        await _notify_failure("daily_lead_scoring", str(e))


async def _run_weekly_status_email():
    """Send the weekly status email batch. Skips when admin disabled the kind."""
    if not _email_schedule_enabled("weekly_status"):
        logger.info("[scheduler] weekly_status_email disabled by admin; skipping run")
        _record("weekly_status_email", "skipped", "disabled")
        return
    logger.info("[scheduler] Running weekly status email batch...")
    try:
        from shared.weekly_email import send_weekly_status_for_all
        result = await asyncio.to_thread(send_weekly_status_for_all)
        sent = result.get("sent", 0)
        skipped = result.get("skipped", 0)
        errors = result.get("errors", 0)
        logger.info(
            f"[scheduler] Weekly status email done -- sent={sent} skipped={skipped} errors={errors}"
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


async def _refresh_content_for_tenant(tenant_id: str, sb) -> tuple:
    """Ensure the next 2 upcoming plan items have written articles, and append one new idea."""
    from datetime import date, timedelta
    import json as _json
    import anthropic
    from shared.config import settings as _settings

    today_str = date.today().isoformat()
    written = 0
    added = 0

    brand: dict = {}
    try:
        row = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
        brand = (row.data or {}).get("settings", {}) if row.data else {}
    except Exception:
        pass

    brand_name = brand.get("brand_name") or ""
    brand_desc = brand.get("brand_description") or ""
    target_audience = brand.get("target_audience") or ""
    content_language = brand.get("content_language") or "sv"

    # Step 1: ensure next 2 upcoming plan items have a written piece
    upcoming = (
        sb.table("content_plan_items")
        .select("*")
        .eq("tenant_id", tenant_id)
        .not_.is_("scheduled_for", "null")
        .gte("scheduled_for", today_str)
        .is_("content_piece_id", "null")
        .order("scheduled_for", desc=False)
        .limit(2)
        .execute()
    )
    items_to_fill = upcoming.data or []

    if items_to_fill:
        client = anthropic.Anthropic(api_key=_settings.ANTHROPIC_API_KEY)
        for item in items_to_fill:
            try:
                title = item.get("title") or "Article"
                keyword = (item.get("target_keyword") or "").strip()
                topic = item.get("topic") or keyword or title
                ctype = item.get("content_type") or "blog_article"

                # Prefer linking an existing onboarding draft with matching keyword
                if keyword:
                    ex = (
                        sb.table("content_pieces")
                        .select("id")
                        .eq("tenant_id", tenant_id)
                        .eq("target_keyword", keyword)
                        .eq("status", "draft")
                        .eq("source", "onboarding")
                        .is_("content_plan_item_id", "null")
                        .limit(1)
                        .execute()
                    )
                    if ex.data:
                        sb.table("content_plan_items").update(
                            {"status": "draft", "content_piece_id": ex.data[0]["id"]}
                        ).eq("id", item["id"]).execute()
                        written += 1
                        continue

                # Write a fresh article with Claude
                msg = client.messages.create(
                    model=_settings.CLAUDE_MODEL,
                    max_tokens=8000,
                    system=(
                        "You are a senior content writer. Write a high-quality SEO blog post. "
                        "1500-2500 words, Markdown only (no HTML). Use ## for H2, ### for H3. "
                        "Start with a 2-3 sentence engaging intro (no H1). "
                        "4-7 H2 sections, weave the target keyword naturally. "
                        "Include a 'Key takeaways' section near the end. "
                        "End with a conclusion referencing the brand. "
                        f"Write in: {content_language}. Output ONLY the markdown body."
                    ),
                    messages=[{"role": "user", "content": (
                        f"Brand: {brand_name}\nDescription: {brand_desc}\n"
                        f"Target audience: {target_audience}\nYear: {date.today().year}\n"
                        f"Title: \"{title}\"\nTarget keyword: {keyword}\nAngle: {topic}"
                    )}],
                )
                body_md = msg.content[0].text.strip()
                word_count = len(body_md.split())
                first_para = next(
                    (p for p in body_md.split("\n\n") if len(p.strip()) > 60), ""
                )
                inserted = sb.table("content_pieces").insert({
                    "tenant_id": tenant_id,
                    "title": title,
                    "content": body_md,
                    "content_type": ctype,
                    "meta_title": title[:60],
                    "meta_description": first_para.replace("\n", " ").strip()[:160],
                    "target_keyword": keyword or None,
                    "word_count": word_count,
                    "status": "draft",
                    "source": "daily_refresh",
                }).execute()
                piece_id = ((inserted.data or [{}])[0]).get("id")
                if piece_id:
                    sb.table("content_plan_items").update(
                        {"status": "draft", "content_piece_id": piece_id}
                    ).eq("id", item["id"]).execute()
                written += 1
            except Exception as e:
                logger.warning(f"[content_refresh] write failed for item {item.get('id')}: {e}")

    # Step 2: add one new calendar suggestion for the day after the last scheduled entry
    try:
        last_res = (
            sb.table("content_plan_items")
            .select("scheduled_for")
            .eq("tenant_id", tenant_id)
            .not_.is_("scheduled_for", "null")
            .order("scheduled_for", desc=True)
            .limit(1)
            .execute()
        )
        last_row = (last_res.data or [{}])[0]
        if not last_row.get("scheduled_for"):
            return written, added

        last_date = date.fromisoformat(last_row["scheduled_for"][:10])
        next_date = last_date + timedelta(days=1)

        kw_res = (
            sb.table("content_plan_items")
            .select("target_keyword")
            .eq("tenant_id", tenant_id)
            .not_.is_("target_keyword", "null")
            .limit(200)
            .execute()
        )
        used_kws = {(r.get("target_keyword") or "").strip().lower() for r in (kw_res.data or [])}

        client = anthropic.Anthropic(api_key=_settings.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=_settings.CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": (
                f"Brand: {brand_name}\nDescription: {brand_desc}\n"
                f"Audience: {target_audience}\nLanguage: {content_language}\n"
                f"Keywords already planned (avoid): {', '.join(list(used_kws)[:30])}\n\n"
                "Generate ONE new blog post idea with a fresh keyword not listed above. "
                "Return ONLY valid JSON (no markdown): "
                '{"title":"...","target_keyword":"...","topic":"one sentence angle"}'
            )}],
        )
        raw = msg.content[0].text.strip()
        try:
            idea = _json.loads(raw)
        except Exception:
            idea = _json.loads(raw.split("```")[1].lstrip("json").strip()) if "```" in raw else {}

        sb.table("content_plan_items").insert({
            "tenant_id": tenant_id,
            "title": str(idea.get("title") or "New content idea")[:300],
            "topic": str(idea.get("topic") or "")[:1000],
            "content_type": "blog_article",
            "target_keyword": str(idea.get("target_keyword") or "")[:200] or None,
            "priority": "medium",
            "status": "idea",
            "source": "daily_refresh",
            "scheduled_for": f"{next_date.isoformat()}T09:00:00.000Z",
        }).execute()
        added = 1
    except Exception as e:
        logger.warning(f"[content_refresh] suggestion failed for tenant={tenant_id}: {e}")

    return written, added


async def _run_daily_content_refresh():
    """Maintain two-ahead invariant: ensure next 2 plan items always have written articles."""
    job_id = "daily_content_refresh"
    logger.info("[scheduler] Running daily content refresh...")
    try:
        from shared.database import get_supabase
        sb = get_supabase()

        # Collect all tenants that have any content plan items
        tenants_res = (
            sb.table("content_plan_items")
            .select("tenant_id")
            .limit(500)
            .execute()
        )
        tenant_ids = list({
            r["tenant_id"] for r in (tenants_res.data or []) if r.get("tenant_id")
        })
        logger.info(f"[content_refresh] processing {len(tenant_ids)} tenant(s)")

        total_written = 0
        total_added = 0
        for tenant_id in tenant_ids:
            try:
                w, a = await _refresh_content_for_tenant(tenant_id, sb)
                total_written += w
                total_added += a
            except Exception as e:
                logger.warning(f"[content_refresh] tenant={tenant_id} failed: {e}")

        logger.info(
            f"[content_refresh] done -- wrote {total_written} articles, "
            f"added {total_added} calendar suggestions"
        )
        _record(job_id, "success")
    except Exception as e:
        logger.error(f"[scheduler] {job_id} failed: {e}")
        _record(job_id, "error", str(e))
        await _notify_failure(job_id, str(e))


async def _run_for_all_tenants(agent_name: str, schedule: str) -> None:
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


async def _run_due_content_drafts():
    """Process content_plan_items where scheduled_for <= now()."""
    job_id = "hourly_due_content_drafts"
    try:
        from api.routes.content_plan import process_due_scheduled_items
        stats = await process_due_scheduled_items()
        if stats.get("drafted") or stats.get("published") or stats.get("failed"):
            logger.info(
                f"[scheduler] due-content-drafts -- drafted {stats.get('drafted',0)}, "
                f"published {stats.get('published',0)}, failed {stats.get('failed',0)}"
            )
        _record(job_id, "success")
    except Exception as e:
        logger.error(f"[scheduler] {job_id} failed: {e}")
        _record(job_id, "error", str(e))
        await _notify_failure(job_id, str(e))


async def _run_social_posts_email():
    """Send social-posts emails 24h after each article publishes.

    Looks for content_pieces with status='published' whose published_at
    is more than 24h ago and which have social children that haven't
    been emailed yet. Fills in the article URL and ships one HTML email
    per article via Brevo.
    """
    if not _email_schedule_enabled("social_posts"):
        logger.info("[scheduler] hourly_social_posts_email disabled by admin; skipping run")
        _record("hourly_social_posts_email", "skipped", "disabled")
        return
    job_id = "hourly_social_posts_email"
    try:
        from shared.social_email_dispatcher import dispatch_due_social_emails
        stats = await dispatch_due_social_emails()
        if stats.get("sent") or stats.get("errors"):
            logger.info(
                f"[scheduler] social-posts-email -- checked {stats.get('checked',0)}, "
                f"sent {stats.get('sent',0)}, skipped {stats.get('skipped',0)}, "
                f"errors {stats.get('errors',0)}"
            )
        _record(job_id, "success")
    except Exception as e:
        logger.error(f"[scheduler] {job_id} failed: {e}")
        _record(job_id, "error", str(e))
        await _notify_failure(job_id, str(e))


async def _run_reload_email_schedules():
    """Once a minute, check email_schedules for changes and reschedule jobs.

    Compares the row's `updated_at` against the cached state. When an admin
    edits the schedule via the dashboard, the new cron applies within ~60s
    without restarting the worker.
    """
    for kind, (job_id, *_defaults) in _EMAIL_JOBS.items():
        try:
            row = _read_email_schedule(kind)
            cached = _email_schedule_state.get(kind) or {}
            if not row:
                continue
            if row.get("updated_at") == cached.get("updated_at"):
                continue
            new_trigger = _build_email_trigger(kind)
            try:
                scheduler.reschedule_job(job_id, trigger=new_trigger)
                logger.info(
                    f"[scheduler] reloaded email schedule for {kind}: "
                    f"dow={row.get('cron_day_of_week')} hour={row.get('cron_hour')} "
                    f"minute={row.get('cron_minute')} enabled={row.get('enabled')}"
                )
            except Exception as e:
                logger.warning(f"[scheduler] reschedule_job({job_id}) failed: {e}")
        except Exception as e:
            logger.debug(f"[scheduler] reload check failed for {kind}: {e}")


async def _run_watchdog() -> None:
    try:
        from api.routes.tenant_activation import reap_stale_runs
        n = await reap_stale_runs()
        if n:
            logger.info(f"[scheduler] watchdog reaped {n} stale runs")
    except Exception as e:
        logger.warning(f"[scheduler] watchdog failed: {e}")


def start():
    """Register all jobs and start the scheduler."""
    scheduler.add_job(_run_daily_keyword_tracking, CronTrigger(hour=2, minute=0), id="daily_keyword_tracking", replace_existing=True)
    scheduler.add_job(_run_weekly_seo_audit, CronTrigger(day_of_week="mon", hour=3, minute=0), id="weekly_seo_audit", replace_existing=True)
    scheduler.add_job(_run_daily_workflow, CronTrigger(hour=6, minute=0), id="daily_workflow", replace_existing=True)
    scheduler.add_job(_run_daily_metrics, CronTrigger(hour=4, minute=0), id="daily_metrics", replace_existing=True)
    scheduler.add_job(_run_daily_ads_check, CronTrigger(hour=8, minute=0), id="daily_ads_check", replace_existing=True)
    scheduler.add_job(_run_weekly_content_analysis, CronTrigger(day_of_week="wed", hour=5, minute=0), id="weekly_content_analysis", replace_existing=True)
    # Content autopilot is driven by the dashboard Vercel cron (daily + weekly)
    # via POST /api/tenant/agents/content/trigger — the single source of truth.
    # The internal fan-out is kept behind a flag to avoid duplicate generation.
    if os.getenv("ENABLE_INTERNAL_CONTENT_AUTOPILOT", "").lower() in ("1", "true", "yes"):
        scheduler.add_job(_run_content_autopilot, CronTrigger(day_of_week="wed", hour=6, minute=0), id="weekly_content_autopilot", replace_existing=True)
    scheduler.add_job(_run_due_content_drafts, CronTrigger(minute=0), id="hourly_due_content_drafts", replace_existing=True)
    scheduler.add_job(_run_daily_content_refresh, CronTrigger(hour=7, minute=30), id="daily_content_refresh", replace_existing=True)

    # Email jobs — cron pulled from email_schedules so admin can edit it live.
    weekly_status_job_id, *_ = _EMAIL_JOBS["weekly_status"]
    social_posts_job_id, *_ = _EMAIL_JOBS["social_posts"]
    scheduler.add_job(
        _run_weekly_status_email,
        _build_email_trigger("weekly_status"),
        id=weekly_status_job_id,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_social_posts_email,
        _build_email_trigger("social_posts"),
        id=social_posts_job_id,
        replace_existing=True,
    )

    scheduler.add_job(_run_midday_review_check, CronTrigger(hour=14, minute=0), id="midday_review_check", replace_existing=True)
    scheduler.add_job(_run_daily_reflection, CronTrigger(hour=22, minute=0), id="daily_reflection", replace_existing=True)
    scheduler.add_job(_run_daily_digest, CronTrigger(hour=17, minute=0), id="daily_digest", replace_existing=True)
    scheduler.add_job(_run_weekly_goal_review, CronTrigger(day_of_week="fri", hour=9, minute=0), id="weekly_goal_review", replace_existing=True)
    scheduler.add_job(_run_daily_agent_reports, CronTrigger(hour=5, minute=0), id="daily_agent_reports", replace_existing=True)
    scheduler.add_job(_run_daily_dev_health_check, CronTrigger(hour=5, minute=30), id="daily_dev_health_check", replace_existing=True)
    scheduler.add_job(_run_weekly_social_analysis, CronTrigger(day_of_week="tue", hour=11, minute=0), id="weekly_social_analysis", replace_existing=True)
    scheduler.add_job(_run_daily_lead_scoring, CronTrigger(hour=7, minute=0), id="daily_lead_scoring", replace_existing=True)

    tenant_fanout_jobs = [
        ("seo", "daily", CronTrigger(hour=2, minute=30)),
        ("analytics", "daily", CronTrigger(hour=4, minute=30)),
        ("social", "daily", CronTrigger(hour=6, minute=30)),
        ("reviews", "daily", CronTrigger(hour=14, minute=30)),
        # "content" intentionally omitted: the generic content agent duplicates
        # the autopilot pipeline. Content runs via the dashboard cron trigger.
        ("geo", "weekly", CronTrigger(day_of_week="thu", hour=10, minute=30)),
        ("strategy", "weekly", CronTrigger(day_of_week="sun", hour=18, minute=0)),
    ]
    for agent_name, schedule_kind, trigger in tenant_fanout_jobs:
        job_id = f"tenants_{agent_name}_{schedule_kind}"
        scheduler.add_job(_run_for_all_tenants, trigger, args=[agent_name, schedule_kind], id=job_id, replace_existing=True)

    scheduler.add_job(_run_watchdog, CronTrigger(minute="*/5"), id="agent_runs_watchdog", replace_existing=True)
    scheduler.add_job(_run_reload_email_schedules, IntervalTrigger(seconds=60), id="email_schedules_reload", replace_existing=True)

    scheduler.start()
    logger.info(
        "[scheduler] Started -- "
        "keywords 02:00, SEO Mon 03:00, metrics 04:00, "
        "agent-reports 05:00, dev-health 05:30, workflow 06:00, lead-scoring 07:00, "
        "content-refresh 07:30, ads 08:00, "
        "social Tue 11:00, reviews 14:00, content-analysis Wed 05:00, "
        "content-autopilot via dashboard cron, "
        "due-content-drafts hourly :00, social-emails admin-configurable, "
        "weekly-status admin-configurable, "
        "digest 17:00, reflection 22:00, goals Fri 09:00 (UTC)"
    )


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped")
