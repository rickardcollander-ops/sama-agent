"""
Thin wrapper around the Resend Python SDK.

Resend is the configured transactional email provider. The wrapper exists so
that the rest of the code never imports `resend` directly — that keeps test
mocking simple and lets us swap providers later without rewriting callers.
"""

import logging
from typing import Optional

from shared.config import settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when Resend is not configured (missing API key or from address)."""


def _from_header() -> str:
    name = (settings.EMAIL_FROM_NAME or "").strip()
    address = (settings.EMAIL_FROM_ADDRESS or "").strip()
    if not address:
        raise EmailNotConfigured("EMAIL_FROM_ADDRESS is not set")
    return f"{name} <{address}>" if name else address


def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    reply_to: Optional[str] = None,
    tags: Optional[list[dict]] = None,
) -> dict:
    """Send a single transactional email via Resend.

    Returns the Resend API response (contains the message id). Raises
    `EmailNotConfigured` if the provider is not configured, or any exception
    raised by the Resend SDK on transport errors.
    """
    if not settings.RESEND_API_KEY:
        raise EmailNotConfigured("RESEND_API_KEY is not set")

    import resend  # type: ignore

    resend.api_key = settings.RESEND_API_KEY

    params: dict = {
        "from": _from_header(),
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        params["text"] = text
    if reply_to:
        params["reply_to"] = reply_to
    if tags:
        params["tags"] = tags

    response = resend.Emails.send(params)
    logger.info(f"[email] Sent to={to} subject={subject!r} id={response.get('id') if isinstance(response, dict) else response}")
    return response if isinstance(response, dict) else {"id": str(response)}
