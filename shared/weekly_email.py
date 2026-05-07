"""
Weekly status email — composer + sender.

Run weekly (Monday 09:00 UTC) by the scheduler. Per opted-in user, aggregates
the past 7 days of agent_reports and pending content_pieces across the user's
sites, renders the email and sends via Resend.

Dashboard exposes a "Send test email now" button that calls
`send_weekly_status_for_user(..., test=True)` from an authenticated route.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

from shared.config import settings
from shared.database import get_supabase
from shared.email import resend_client
from shared.email.template import (
    render_weekly_status_html,
    render_weekly_status_text,
)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

LOOKBACK_DAYS = 7
PENDING_APPROVAL_LIMIT = 50
# How recently a non-test send blocks another non-test send. 6 days is short
# enough to allow a Monday → Monday cadence even with a few hours of drift,
# and long enough to absorb a duplicated scheduler tick or an ops re-run.
DEDUPE_WINDOW_DAYS = 6

# Order matches the dashboard sidebar so users see a familiar shape.
AGENT_ORDER = ("seo", "content", "ads", "social", "reviews", "analytics", "ai_visibility")


# ── Data assembly ────────────────────────────────────────────────────────────


def _week_label(now: datetime) -> str:
    """Swedish week label, e.g. 'v.19 · 5–11 maj 2026'."""
    start = now - timedelta(days=LOOKBACK_DAYS - 1)
    iso_week = now.isocalendar().week
    months_sv = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
    if start.month == now.month:
        return f"v.{iso_week} · {start.day}–{now.day} {months_sv[now.month - 1]} {now.year}"
    return f"v.{iso_week} · {start.day} {months_sv[start.month - 1]}–{now.day} {months_sv[now.month - 1]} {now.year}"


def _resolve_user_sites(user_id: str) -> list[dict]:
    """Return list of {site_id, site_domain, brand_name} for a user.

    Falls back to a single pseudo-site keyed by user_id if user_sites is empty
    so that the email can still be assembled for users on the legacy
    single-tenant model.
    """
    sb = get_supabase()
    try:
        rows = (
            sb.table("user_sites")
            .select("site_id, site_domain, brand_name, account_id")
            .eq("user_id", user_id)
            .execute()
        )
        sites = [r for r in (rows.data or []) if r.get("site_id")]
        if sites:
            return sites
    except Exception as e:
        logger.warning(f"[weekly-email] user_sites lookup failed for {user_id}: {e}")
    return [{"site_id": user_id, "site_domain": "", "brand_name": "", "account_id": user_id}]


def _fetch_agent_reports(tenant_ids: list[str], since: datetime) -> list[dict]:
    """Pull last 7 days of agent_reports for the given tenant_ids."""
    if not tenant_ids:
        return []
    sb = get_supabase()
    try:
        result = (
            sb.table("agent_reports")
            .select("agent_name, summary, highlights, problems, improvements, stats, created_at, tenant_id")
            .in_("tenant_id", tenant_ids)
            .gte("created_at", since.isoformat())
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"[weekly-email] agent_reports query failed: {e}")
        return []


def _fetch_pending_content(tenant_ids: list[str]) -> list[dict]:
    """Pull draft content_pieces awaiting approval."""
    if not tenant_ids:
        return []
    sb = get_supabase()
    try:
        # content_pieces may not be tenant-scoped on every install — try with
        # tenant filter first, fall back to global query if the column isn't
        # present (defensive against schema drift).
        result = (
            sb.table("content_pieces")
            .select("id, title, content_type, status, created_at")
            .eq("status", "draft")
            .in_("tenant_id", tenant_ids)
            .order("created_at", desc=True)
            .limit(PENDING_APPROVAL_LIMIT)
            .execute()
        )
        return result.data or []
    except Exception:
        try:
            result = (
                sb.table("content_pieces")
                .select("id, title, content_type, status, created_at")
                .eq("status", "draft")
                .order("created_at", desc=True)
                .limit(PENDING_APPROVAL_LIMIT)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.warning(f"[weekly-email] content_pieces query failed: {e}")
            return []


def _aggregate_by_agent(reports: list[dict]) -> list[dict]:
    """Collapse per-day reports into one section per agent.

    Picks the latest summary and unions all highlights / problems from the
    week. Caps highlights to keep the email scannable.
    """
    by_agent: dict[str, dict] = {}
    for r in reports:
        agent = (r.get("agent_name") or "").lower()
        if not agent:
            continue
        bucket = by_agent.setdefault(
            agent,
            {"agent": agent, "summary": "", "highlights": [], "problems": [], "_seen": set()},
        )
        if not bucket["summary"] and r.get("summary"):
            bucket["summary"] = r["summary"]
        for h in r.get("highlights") or []:
            text = h if isinstance(h, str) else h.get("text") if isinstance(h, dict) else None
            if text and text not in bucket["_seen"]:
                bucket["highlights"].append(text)
                bucket["_seen"].add(text)
        for p in r.get("problems") or []:
            text = p if isinstance(p, str) else p.get("text") if isinstance(p, dict) else None
            if text and text not in bucket["_seen"]:
                bucket["problems"].append(text)
                bucket["_seen"].add(text)

    ordered: list[dict] = []
    for agent in AGENT_ORDER:
        if agent in by_agent:
            data = by_agent.pop(agent)
            data.pop("_seen", None)
            ordered.append(data)
    for remaining in by_agent.values():
        remaining.pop("_seen", None)
        ordered.append(remaining)
    return ordered


def _collect_problems(agent_sections: list[dict]) -> list[str]:
    """Flatten problems across agents into a top-level list (deduped, capped)."""
    seen: set[str] = set()
    out: list[str] = []
    for s in agent_sections:
        for p in s.get("problems", []):
            if p not in seen:
                out.append(p)
                seen.add(p)
            if len(out) >= 5:
                return out
    return out


# ── Email composition ────────────────────────────────────────────────────────


def _compose_email(
    *,
    user_id: str,
    recipient_email: str,
    user_brand_name: str = "",
    now: Optional[datetime] = None,
) -> dict:
    """Assemble subject + html + text + meta for one user."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)

    sites = _resolve_user_sites(user_id)
    tenant_ids = [s["site_id"] for s in sites if s.get("site_id")]
    brand_name = (
        user_brand_name
        or next((s.get("brand_name") for s in sites if s.get("brand_name")), "")
        or "Din verksamhet"
    )

    reports = _fetch_agent_reports(tenant_ids, since)
    pending = _fetch_pending_content(tenant_ids)
    agent_sections = _aggregate_by_agent(reports)
    problems = _collect_problems(agent_sections)

    nothing_happened = not agent_sections and not pending and not problems

    dashboard_base = settings.DASHBOARD_BASE_URL.rstrip("/")
    qs = urlencode({"utm_source": "weekly_email", "utm_medium": "email"})
    dashboard_url = f"{dashboard_base}/?{qs}"
    approvals_url = f"{dashboard_base}/approvals?{qs}"
    unsubscribe_url = f"{dashboard_base}/settings/notifications?{qs}"

    week_label = _week_label(now)
    subject = (
        f"Veckostatus: {len(pending)} att granska · {brand_name}"
        if pending
        else f"Veckostatus · {brand_name}"
    )

    html = render_weekly_status_html(
        brand_name=brand_name,
        week_label=week_label,
        agent_sections=agent_sections,
        pending_approvals=pending,
        problems=problems,
        dashboard_url=dashboard_url,
        approvals_url=approvals_url,
        unsubscribe_url=unsubscribe_url,
        nothing_happened=nothing_happened,
    )
    text = render_weekly_status_text(
        brand_name=brand_name,
        week_label=week_label,
        agent_sections=agent_sections,
        pending_approvals=pending,
        problems=problems,
        dashboard_url=dashboard_url,
        approvals_url=approvals_url,
        nothing_happened=nothing_happened,
    )

    return {
        "subject": subject,
        "html": html,
        "text": text,
        "recipient": recipient_email,
        "user_id": user_id,
        "tenant_ids": tenant_ids,
        "stats": {
            "agent_count": len(agent_sections),
            "pending_count": len(pending),
            "problem_count": len(problems),
            "nothing_happened": nothing_happened,
        },
    }


# ── Recipient resolution ─────────────────────────────────────────────────────


def _resolve_recipient_email(user_id: str, override: Optional[str]) -> Optional[str]:
    """Return the email to send to. Service-role required to read auth.users."""
    if override:
        return override.strip() or None
    sb = get_supabase()
    try:
        user = sb.auth.admin.get_user_by_id(user_id)  # type: ignore[attr-defined]
        email = getattr(getattr(user, "user", None), "email", None)
        if email:
            return email
    except Exception as e:
        logger.warning(f"[weekly-email] auth.admin.get_user_by_id failed for {user_id}: {e}")
    return None


def _settings_for_user(user_id: str) -> dict:
    sb = get_supabase()
    try:
        row = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return (row.data or {}).get("settings") or {}
    except Exception:
        return {}


def _recently_sent(user_id: str) -> Optional[str]:
    """Return the ISO timestamp of the last successful, non-test weekly send
    within the dedupe window, or None if there is none.

    Used to make `send_weekly_status_for_all` idempotent if the scheduler
    fires twice (manual run-now plus the cron tick, or a process restart that
    happens to land on the cron minute).
    """
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUPE_WINDOW_DAYS)).isoformat()
    try:
        result = (
            sb.table("email_send_log")
            .select("created_at")
            .eq("user_id", user_id)
            .eq("kind", "weekly_status")
            .eq("status", "sent")
            .eq("test", False)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0]["created_at"] if rows else None
    except Exception as e:
        # If the table doesn't exist yet (migration not applied), treat as
        # "never sent" so the scheduler still works on day one.
        logger.warning(f"[weekly-email] dedupe lookup failed: {e}")
        return None


# ── Public API ───────────────────────────────────────────────────────────────


def send_weekly_status_for_user(
    user_id: str,
    *,
    recipient_override: Optional[str] = None,
    test: bool = False,
) -> dict:
    """Send (or attempt to send) the weekly status email for one user.

    Returns a dict describing the outcome — never raises for missing
    config / missing recipient (those are returned as `skipped: True` so a
    batch run can continue past them).
    """
    user_settings = _settings_for_user(user_id)
    notif = (user_settings.get("notification_preferences") or {}) if isinstance(user_settings, dict) else {}
    override = recipient_override or notif.get("recipient_override")

    recipient = _resolve_recipient_email(user_id, override)
    if not recipient:
        logger.info(f"[weekly-email] no recipient for user {user_id}; skipping")
        return {"sent": False, "skipped": True, "reason": "no_recipient", "user_id": user_id}

    # Dedupe: skip if we already sent a real (non-test) weekly within the
    # dedupe window. Test sends bypass this so the dashboard "send test"
    # button always works.
    if not test:
        last_sent = _recently_sent(user_id)
        if last_sent:
            logger.info(f"[weekly-email] already sent to {user_id} at {last_sent}; skipping")
            return {
                "sent": False,
                "skipped": True,
                "reason": "already_sent_this_week",
                "user_id": user_id,
                "last_sent_at": last_sent,
            }

    composed = _compose_email(
        user_id=user_id,
        recipient_email=recipient,
        user_brand_name=str(user_settings.get("brand_name") or ""),
    )

    if test:
        composed["subject"] = f"[TEST] {composed['subject']}"

    if not settings.RESEND_API_KEY or not settings.EMAIL_FROM_ADDRESS:
        logger.warning("[weekly-email] Resend not configured; not sending")
        return {
            "sent": False,
            "skipped": True,
            "reason": "provider_not_configured",
            "user_id": user_id,
            "subject": composed["subject"],
            "stats": composed["stats"],
        }

    try:
        response = resend_client.send_email(
            to=recipient,
            subject=composed["subject"],
            html=composed["html"],
            text=composed["text"],
            tags=[{"name": "kind", "value": "weekly_status"}],
        )
        message_id = response.get("id") if isinstance(response, dict) else None
        _log_send(
            user_id=user_id,
            recipient=recipient,
            subject=composed["subject"],
            stats=composed["stats"],
            status="sent",
            message_id=message_id,
            test=test,
        )
        return {
            "sent": True,
            "user_id": user_id,
            "recipient": recipient,
            "message_id": message_id,
            "subject": composed["subject"],
            "stats": composed["stats"],
        }
    except Exception as e:
        logger.error(f"[weekly-email] send failed for {user_id}: {e}")
        _log_send(
            user_id=user_id,
            recipient=recipient,
            subject=composed["subject"],
            stats=composed["stats"],
            status="error",
            error=str(e)[:500],
            test=test,
        )
        return {"sent": False, "user_id": user_id, "error": str(e)}


def send_weekly_status_for_all() -> dict:
    """Iterate every user with weekly_email_enabled=true and send.

    Run by the scheduler at the configured cron time.
    """
    sb = get_supabase()
    try:
        rows = (
            sb.table("user_settings")
            .select("user_id, settings")
            .execute()
        )
    except Exception as e:
        logger.error(f"[weekly-email] could not load user_settings: {e}")
        return {"sent": 0, "skipped": 0, "errors": 0, "error": str(e)}

    sent = skipped = errors = 0
    results: list[dict] = []
    for row in rows.data or []:
        s = row.get("settings") or {}
        notif = s.get("notification_preferences") if isinstance(s, dict) else None
        if not (isinstance(notif, dict) and notif.get("weekly_email_enabled")):
            continue
        outcome = send_weekly_status_for_user(row["user_id"])
        results.append(outcome)
        if outcome.get("sent"):
            sent += 1
        elif outcome.get("skipped"):
            skipped += 1
        else:
            errors += 1

    logger.info(f"[weekly-email] batch done — sent={sent} skipped={skipped} errors={errors}")
    return {"sent": sent, "skipped": skipped, "errors": errors, "results": results}


def preview_weekly_status_for_user(user_id: str) -> dict:
    """Compose the email without sending. Used by the dashboard preview UI."""
    recipient = _resolve_recipient_email(user_id, None) or "preview@example.com"
    user_settings = _settings_for_user(user_id)
    return _compose_email(
        user_id=user_id,
        recipient_email=recipient,
        user_brand_name=str(user_settings.get("brand_name") or ""),
    )


# ── Send log ─────────────────────────────────────────────────────────────────


def _log_send(
    *,
    user_id: str,
    recipient: str,
    subject: str,
    stats: dict[str, Any],
    status: str,
    message_id: Optional[str] = None,
    error: Optional[str] = None,
    test: bool = False,
) -> None:
    """Write to email_send_log. Best-effort — never raises."""
    sb = get_supabase()
    try:
        sb.table("email_send_log").insert(
            {
                "user_id": user_id,
                "recipient": recipient,
                "kind": "weekly_status",
                "subject": subject,
                "status": status,
                "message_id": message_id,
                "error": error,
                "stats": stats,
                "test": test,
            }
        ).execute()
    except Exception as e:
        logger.warning(f"[weekly-email] could not write email_send_log: {e}")
