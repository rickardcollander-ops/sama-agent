"""
Notification Service for SAMA 2.0
Sends alerts and digests via Slack webhook (primary) and email (future).
"""

import logging
import httpx
from typing import Dict, Any, Optional, List
from datetime import datetime

from shared.config import settings

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Send notifications via Slack incoming webhook."""

    SEVERITY_EMOJI = {
        "critical": ":red_circle:",
        "high": ":large_orange_circle:",
        "warning": ":warning:",
        "medium": ":large_yellow_circle:",
        "info": ":large_blue_circle:",
        "success": ":white_check_mark:",
    }

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or getattr(settings, "SLACK_WEBHOOK_URL", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    async def send(
        self,
        title: str,
        message: str,
        severity: str = "info",
        agent: str = "system",
        fields: Optional[Dict[str, str]] = None,
    ) -> bool:
        if not self.is_configured:
            logger.debug("[slack] Not configured — skipping notification")
            return False

        emoji = self.SEVERITY_EMOJI.get(severity, ":information_source:")
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {title}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            },
        ]

        if fields:
            field_blocks = [
                {"type": "mrkdwn", "text": f"*{k}:* {v}"}
                for k, v in fields.items()
            ]
            blocks.append({"type": "section", "fields": field_blocks})

        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Agent: *{agent}* | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"}
            ],
        })

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self.webhook_url,
                    json={"blocks": blocks},
                )
                if resp.status_code == 200:
                    logger.info(f"[slack] Sent: {title}")
                    return True
                else:
                    logger.warning(f"[slack] Failed ({resp.status_code}): {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"[slack] Error sending notification: {e}")
            return False

    async def send_daily_digest(self, summary: Dict[str, Any]) -> bool:
        """Send a daily activity digest to Slack."""
        actions_count = summary.get("actions_executed", 0)
        pending = summary.get("pending_actions", 0)
        alerts = summary.get("alerts", 0)
        wins = summary.get("wins", [])

        lines = [
            f"*Actions executed:* {actions_count}",
            f"*Pending approvals:* {pending}",
            f"*Alerts raised:* {alerts}",
        ]

        if wins:
            lines.append("\n*Wins:*")
            for w in wins[:5]:
                lines.append(f"  :trophy: {w}")

        return await self.send(
            title="SAMA Daily Digest",
            message="\n".join(lines),
            severity="info",
            agent="orchestrator",
        )


class NotificationService:
    """Unified notification dispatcher."""

    def __init__(self):
        self.slack = SlackNotifier()

    @property
    def is_configured(self) -> bool:
        return self.slack.is_configured

    async def notify(
        self,
        title: str,
        message: str,
        severity: str = "info",
        agent: str = "system",
        fields: Optional[Dict[str, str]] = None,
    ) -> bool:
        sent = False

        if self.slack.is_configured:
            sent = await self.slack.send(title, message, severity, agent, fields)

        if not sent:
            logger.info(f"[notify] (no channels configured) {severity}: {title} — {message}")

        return sent

    async def send_daily_digest(self, summary: Dict[str, Any]) -> bool:
        if self.slack.is_configured:
            return await self.slack.send_daily_digest(summary)
        return False


# Global instance
notification_service = NotificationService()
