"""Shared writer for email_send_log.

Both weekly_email (Resend) and social_posts_email (Brevo) call into
this so the admin email log surfaces every transactional email the
agent sends, regardless of provider.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from shared.database import get_supabase

logger = logging.getLogger(__name__)


def write(
    *,
    kind: str,
    recipient: str,
    subject: str,
    status: str,
    user_id: Optional[str] = None,
    message_id: Optional[str] = None,
    error: Optional[str] = None,
    stats: Optional[dict[str, Any]] = None,
    test: bool = False,
) -> None:
    """Best-effort insert into email_send_log. Never raises.

    `status` is 'sent' | 'error'. `kind` is a stable identifier the
    admin UI groups by ('weekly_status', 'social_posts', ...).
    """
    sb = get_supabase()
    payload: dict[str, Any] = {
        "recipient": recipient,
        "kind": kind,
        "subject": subject or "",
        "status": status,
        "stats": stats or {},
        "test": test,
    }
    if user_id:
        payload["user_id"] = user_id
    if message_id:
        payload["message_id"] = message_id
    if error:
        payload["error"] = error[:1000]
    try:
        sb.table("email_send_log").insert(payload).execute()
    except Exception as e:
        logger.warning(f"[send_log] could not write {kind} log: {e}")
