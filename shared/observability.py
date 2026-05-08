"""
Observability bootstrap: Sentry + structured logging with PII redaction.

Importing this module is a no-op until ``init_sentry()`` is called from the
FastAPI lifespan. The init checks ``SENTRY_DSN`` — if unset, Sentry is
skipped silently so tests/dev environments don't need it.

PII redaction: ``before_send`` strips known sensitive headers, redacts
``Authorization`` / ``Cookie``, and hashes email/phone-shaped values inside
exception messages and breadcrumbs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-sama-account-id",
    "x-sama-site-id",
    "x-tenant-id",
    "x-api-key",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9_\-.=]+", re.I)


def _redact_text(text: str) -> str:
    text = _EMAIL_RE.sub("[email]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


def _scrub(event: dict[str, Any]) -> dict[str, Any]:
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            if key.lower() in _SENSITIVE_HEADERS:
                headers[key] = "[redacted]"
    request["headers"] = headers
    if "data" in request and isinstance(request["data"], str):
        request["data"] = _redact_text(request["data"])
    event["request"] = request

    for ex in (event.get("exception") or {}).get("values") or []:
        if isinstance(ex.get("value"), str):
            ex["value"] = _redact_text(ex["value"])

    breadcrumbs = (event.get("breadcrumbs") or {}).get("values") or []
    for b in breadcrumbs:
        if isinstance(b.get("message"), str):
            b["message"] = _redact_text(b["message"])
        data = b.get("data")
        if isinstance(data, dict):
            for k, v in list(data.items()):
                if isinstance(v, str):
                    data[k] = _redact_text(v)

    return event


def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry not configured (SENTRY_DSN unset); observability skipped")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry-sdk not installed; cannot init Sentry")
        return

    environment = os.getenv("SENTRY_ENVIRONMENT") or os.getenv("ENVIRONMENT", "production")
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        send_default_pii=False,
        max_breadcrumbs=50,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
        ],
        before_send=lambda event, _hint: _scrub(event),
    )
    logger.info("Sentry initialised env=%s", environment)


class PiiRedactingFilter(logging.Filter):
    """Logging filter that scrubs email/phone/bearer tokens from log output."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    _redact_text(a) if isinstance(a, str) else a for a in record.args
                )
            except Exception:
                pass
        return True


def install_logging_redaction() -> None:
    """Install ``PiiRedactingFilter`` on the root logger."""
    root = logging.getLogger()
    flt = PiiRedactingFilter()
    for handler in root.handlers:
        handler.addFilter(flt)
    if not root.handlers:
        # Fallback when basicConfig hasn't run yet.
        logging.basicConfig(level=logging.INFO)
        for handler in logging.getLogger().handlers:
            handler.addFilter(flt)
