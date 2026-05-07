"""
Brevo (Sendinblue) integration -- contact lists + transactional emails.
"""

import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BREVO_API = "https://api.brevo.com/v3"

LIST_SEO_LEADS = 1
LIST_CONTENT_LEADS = 2
LIST_SOCIAL_LEADS = 3
LIST_COMPARISON_LEADS = 4


def _get_list_for_source(source: str, source_url: str) -> int:
    if "/vs/" in source_url:
        return LIST_COMPARISON_LEADS
    if source in ("google", "bing"):
        return LIST_SEO_LEADS
    if source in ("twitter", "linkedin", "facebook", "reddit"):
        return LIST_SOCIAL_LEADS
    return LIST_CONTENT_LEADS


async def add_contact_and_trigger(
    email: str,
    company: str = "",
    source: str = "",
    source_url: str = "",
    name: str = "",
):
    """Add a contact and assign to the appropriate nurture list."""
    from shared.config import settings
    api_key = settings.BREVO_API_KEY
    if not api_key:
        logger.debug("Brevo API key not configured, skipping email nurture")
        return

    list_id = _get_list_for_source(source, source_url)

    payload = {
        "email": email,
        "attributes": {
            "COMPANY": company,
            "SOURCE": source,
            "SOURCE_URL": source_url,
        },
        "listIds": [list_id],
        "updateEnabled": True,
    }
    if name:
        parts = name.split(" ", 1)
        payload["attributes"]["FIRSTNAME"] = parts[0]
        if len(parts) > 1:
            payload["attributes"]["LASTNAME"] = parts[1]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BREVO_API}/contacts",
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
            if resp.status_code in (200, 201, 204):
                logger.info(f"Brevo: added {email} to list {list_id}")
            else:
                logger.warning(f"Brevo: failed to add contact ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Brevo API error: {e}")


async def send_transactional_email(
    to_email: str,
    subject: str,
    html_body: str,
    *,
    sender_email: Optional[str] = None,
    sender_name: Optional[str] = None,
) -> bool:
    """Send a one-off transactional email via Brevo's /smtp/email endpoint.

    Returns True on a 2xx response, False otherwise. Logs but does not raise
    so callers can handle a failed send (e.g. unconfigured Brevo key) gracefully.
    """
    from shared.config import settings
    api_key = settings.BREVO_API_KEY
    if not api_key:
        logger.warning("Brevo API key not configured; skipping transactional email")
        return False

    sender = {
        "email": sender_email or getattr(settings, "BREVO_SENDER_EMAIL", "hello@successifier.com"),
        "name": sender_name or getattr(settings, "BREVO_SENDER_NAME", "SAMA"),
    }

    payload = {
        "sender": sender,
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BREVO_API}/smtp/email",
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            if 200 <= resp.status_code < 300:
                logger.info(f"Brevo transactional sent to {to_email} (status {resp.status_code})")
                return True
            logger.warning(
                f"Brevo transactional failed for {to_email}: {resp.status_code} {resp.text[:200]}"
            )
            return False
    except Exception as e:
        logger.error(f"Brevo transactional API error: {e}")
        return False
