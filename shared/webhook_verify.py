"""
Webhook signature verification helpers.

One ``verify_hmac`` per provider so the call sites stay tiny and the algorithm
choices are documented in one place.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_hmac_sha256(*, secret: str, body: bytes, signature: str, prefix: str = "") -> bool:
    """Generic SHA256 HMAC verification.

    ``prefix`` is e.g. ``"sha256="`` for some providers; trimmed before compare.
    """
    if not secret or not signature:
        return False
    sig = signature
    if prefix and sig.lower().startswith(prefix.lower()):
        sig = sig[len(prefix):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return _eq(sig.lower(), expected.lower())


def verify_stripe(*, secret: str, body: bytes, signature_header: str, tolerance_s: int = 300) -> bool:
    """Stripe-style ``t=…,v1=…`` signature header.

    Replay protection: timestamp must be within ``tolerance_s`` of now.
    """
    if not secret or not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    ts = parts.get("t")
    sig = parts.get("v1")
    if not ts or not sig:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > tolerance_s:
        return False
    payload = f"{ts}.".encode() + body
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return _eq(sig, expected)


def verify_github(*, secret: str, body: bytes, signature_header: Optional[str]) -> bool:
    """GitHub uses ``X-Hub-Signature-256: sha256=<hex>``."""
    if not signature_header:
        return False
    return verify_hmac_sha256(
        secret=secret, body=body, signature=signature_header, prefix="sha256="
    )


def verify_slack(*, secret: str, body: bytes, timestamp: Optional[str], signature: Optional[str], tolerance_s: int = 300) -> bool:
    """Slack-style ``v0:<timestamp>:<body>`` signing."""
    if not secret or not signature or not timestamp:
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > tolerance_s:
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return _eq(signature, expected)
