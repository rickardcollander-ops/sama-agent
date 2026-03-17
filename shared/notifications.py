"""
Notification Service for SAMA 2.0
Stores notifications in Supabase for display in the dashboard.
The dashboard is the primary notification channel — no external services needed.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from shared.database import get_supabase

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Stores notifications in a Supabase `notifications` table.
    The dashboard reads from this table and shows them in real-time
    via the Supabase Realtime subscription.
    """

    def __init__(self):
        self._sb = None

    def _get_sb(self):
        if not self._sb:
            self._sb = get_supabase()
        return self._sb

    async def notify(
        self,
        title: str,
        message: str,
        severity: str = "info",
        agent: str = "system",
        fields: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Store a notification for display in the dashboard.

        Severity levels: info, warning, high, critical, success
        """
        try:
            sb = self._get_sb()
            sb.table("notifications").insert({
                "title": title,
                "message": message,
                "severity": severity,
                "agent": agent,
                "fields": fields or {},
                "read": False,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            logger.info(f"[notify] {severity}: {title}")
            return True
        except Exception as e:
            # Table might not exist yet — just log
            logger.info(f"[notify] ({severity}) {title} — {message}")
            logger.debug(f"[notify] DB store failed (table may not exist): {e}")
            return False

    async def send_daily_digest(self, summary: Dict[str, Any]) -> bool:
        """Store a daily digest notification."""
        actions_count = summary.get("actions_executed", 0)
        pending = summary.get("pending_actions", 0)
        wins = summary.get("wins", [])

        lines = [
            f"Actions executed: {actions_count}",
            f"Pending approvals: {pending}",
        ]
        if wins:
            lines.append("Wins: " + ", ".join(wins[:5]))

        return await self.notify(
            title="Daily Digest",
            message=" | ".join(lines),
            severity="info",
            agent="orchestrator",
        )

    async def get_unread(self, limit: int = 20):
        """Get unread notifications for the dashboard."""
        try:
            sb = self._get_sb()
            result = sb.table("notifications") \
                .select("*") \
                .eq("read", False) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            return result.data or []
        except Exception:
            return []

    async def mark_read(self, notification_id: str):
        """Mark a notification as read."""
        try:
            sb = self._get_sb()
            sb.table("notifications").update({
                "read": True,
                "read_at": datetime.utcnow().isoformat(),
            }).eq("id", notification_id).execute()
        except Exception as e:
            logger.debug(f"[notify] Failed to mark read: {e}")

    async def mark_all_read(self):
        """Mark all notifications as read."""
        try:
            sb = self._get_sb()
            sb.table("notifications").update({
                "read": True,
                "read_at": datetime.utcnow().isoformat(),
            }).eq("read", False).execute()
        except Exception as e:
            logger.debug(f"[notify] Failed to mark all read: {e}")


# Global instance
notification_service = NotificationService()
