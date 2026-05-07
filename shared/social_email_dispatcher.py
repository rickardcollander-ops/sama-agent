"""
Dispatch social-posts emails 24h after each article publishes.

For every content_pieces row whose:
  - status = 'published'
  - published_at <= now() - 24 hours (and not too far back)
  - has child rows with content_type LIKE 'social_%'
  - whose plan_item.emailed_at is still NULL
we call send_social_posts_email(article_id) to render + ship the email.

Called hourly from shared.scheduler. Idempotent -- once emailed_at is
set on every social plan_item, the article is skipped on later runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from shared.database import get_supabase
from shared.email.social_posts_email import send_social_posts_email

logger = logging.getLogger(__name__)

# Window: only consider articles published in the last N days. Older
# articles are assumed to have either been emailed already or won't be
# emailed at all (e.g. published before this feature shipped).
LOOKBACK_DAYS = 14


def _resolve_recipient_for_tenant(tenant_id: str) -> str:
    """Find the contact email for a tenant via user_sites.settings."""
    sb = get_supabase()
    for table, key in (("user_sites", "id"), ("user_settings", "user_id")):
        try:
            row = sb.table(table).select("settings").eq(key, tenant_id).single().execute()
            if row.data and isinstance(row.data.get("settings"), dict):
                s = row.data["settings"]
                email = s.get("contact_email") or s.get("email")
                if email:
                    return email
        except Exception:
            continue
    return ""


async def dispatch_due_social_emails() -> Dict[str, int]:
    """Send social-posts emails for any articles whose 24h delay has elapsed."""
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    cutoff_due = (now - timedelta(hours=24)).isoformat()
    cutoff_floor = (now - timedelta(days=LOOKBACK_DAYS)).isoformat()

    stats = {"checked": 0, "sent": 0, "skipped": 0, "errors": 0}

    # 1. Recently-published articles
    try:
        articles = (
            sb.table("content_pieces")
            .select("id,tenant_id,title,published_at")
            .eq("status", "published")
            .lte("published_at", cutoff_due)
            .gte("published_at", cutoff_floor)
            .is_("parent_content_id", "null")
            .limit(200)
            .execute()
        )
    except Exception as e:
        logger.error(f"dispatch_due_social_emails: parent query failed: {e}")
        return stats

    for article in (articles.data or []):
        article_id = article.get("id")
        tenant_id = article.get("tenant_id")
        if not article_id or not tenant_id:
            continue
        stats["checked"] += 1

        # 2. Are there social children that haven't been emailed yet?
        try:
            kids = (
                sb.table("content_pieces")
                .select("id,content_type")
                .eq("parent_content_id", article_id)
                .like("content_type", "social_%")
                .execute()
            )
        except Exception as e:
            logger.debug(f"dispatch_due_social_emails: child query failed for {article_id}: {e}")
            stats["errors"] += 1
            continue

        social_ids = [c["id"] for c in (kids.data or [])]
        if not social_ids:
            stats["skipped"] += 1
            continue

        try:
            plan = (
                sb.table("content_plan_items")
                .select("id,content_piece_id,emailed_at")
                .in_("content_piece_id", social_ids)
                .execute()
            )
        except Exception as e:
            logger.debug(f"dispatch_due_social_emails: plan query failed for {article_id}: {e}")
            stats["errors"] += 1
            continue

        plan_rows = plan.data or []
        if plan_rows and all(p.get("emailed_at") for p in plan_rows):
            stats["skipped"] += 1
            continue

        # 3. Send
        recipient = _resolve_recipient_for_tenant(tenant_id)
        if not recipient:
            logger.warning(f"dispatch_due_social_emails: no recipient for tenant={tenant_id}")
            stats["skipped"] += 1
            continue

        try:
            result = await send_social_posts_email(
                article_content_id=article_id,
                recipient_email=recipient,
            )
            if result.get("sent"):
                stats["sent"] += 1
            else:
                logger.info(
                    f"dispatch_due_social_emails: send_social_posts_email skipped "
                    f"article={article_id} reason={result.get('error')}"
                )
                stats["skipped"] += 1
        except Exception as e:
            logger.error(
                f"dispatch_due_social_emails: send failed for article={article_id}: {e}"
            )
            stats["errors"] += 1

    return stats
