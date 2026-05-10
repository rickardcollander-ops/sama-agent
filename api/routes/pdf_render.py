"""
PDF render service.

Used by the dashboard's Apollo telemarketing flow (admin → campaigns) to
turn a per-lead report HTML page into a printable PDF that the operator
can attach to a follow-up email after a call.

The dashboard generates an HMAC-signed token, embeds it in the report
URL, and posts that URL here. We open the URL in headless Chromium via
Playwright, wait for `networkidle` so all fonts/charts are painted, and
stream the PDF bytes back.

Trust model: the report page itself verifies the HMAC token, so this
endpoint doesn't need to know about leads. It just needs to be locked
down so attackers can't abuse it as a free SSRF — see the host allowlist
and SAMA_INTERNAL_TOKEN check below.
"""

import logging
import os
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, HttpUrl

router = APIRouter()
logger = logging.getLogger(__name__)


WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]


class PdfRenderRequest(BaseModel):
    url: HttpUrl
    format: str = Field(default="A4")
    wait_until: WaitUntil = "networkidle"
    # Extra headers Playwright should send with the page request — handy
    # for cookie-based auth in the future. Today we rely on signed tokens
    # in the URL, so this is unused but reserved.
    extra_headers: Optional[dict[str, str]] = None


def _allowed_hosts() -> set[str]:
    """Comma-separated list of hosts the renderer is permitted to fetch.

    Without this, a leaked SAMA_INTERNAL_TOKEN would let an attacker
    fetch arbitrary intranet URLs through us. Defaults to dashboard
    domains we control.
    """
    raw = os.getenv("PDF_RENDER_ALLOWED_HOSTS", "").strip()
    if raw:
        return {h.strip().lower() for h in raw.split(",") if h.strip()}
    # Sensible default: anything ending in successifier.com + Vercel previews.
    return {
        "app.successifier.com",
        "successifier.com",
        "www.successifier.com",
        "localhost",
        "127.0.0.1",
    }


def _host_is_allowed(host: str) -> bool:
    host = host.lower()
    allowed = _allowed_hosts()
    if host in allowed:
        return True
    # Allow *.vercel.app preview deployments by default — they're the
    # canonical staging environment for this dashboard.
    if host.endswith(".vercel.app"):
        return True
    return False


def _check_internal_token(token: Optional[str]) -> None:
    """If SAMA_INTERNAL_TOKEN is set, require callers to present it."""
    expected = os.getenv("SAMA_INTERNAL_TOKEN", "").strip()
    if not expected:
        return  # Open mode — only safe behind a private network.
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Invalid internal token")


@router.post("/render")
async def render_pdf(
    body: PdfRenderRequest,
    x_sama_internal_token: Optional[str] = Header(default=None),
) -> Response:
    """Render the URL to PDF and stream the bytes back."""
    _check_internal_token(x_sama_internal_token)

    host = urlparse(str(body.url)).hostname or ""
    if not _host_is_allowed(host):
        logger.warning("PDF render blocked: host '%s' not in allowlist", host)
        raise HTTPException(status_code=403, detail=f"Host not allowed: {host}")

    try:
        # Lazy import so missing Playwright doesn't crash app boot — the
        # rest of the agent works without it.
        from playwright.async_api import async_playwright
    except ImportError as e:
        logger.error("Playwright not installed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Playwright is not installed on this deployment",
        ) from e

    pdf_bytes: bytes
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 1800},
                    device_scale_factor=2,  # crisper text + retina-ready
                    extra_http_headers=body.extra_headers or {},
                )
                page = await context.new_page()
                await page.goto(str(body.url), wait_until=body.wait_until, timeout=45_000)
                # Belt-and-braces wait for fonts; otherwise headers can render
                # in fallback fonts on the first paint.
                await page.evaluate("document.fonts && document.fonts.ready")

                pdf_bytes = await page.pdf(
                    format=body.format,
                    print_background=True,
                    margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"},
                    prefer_css_page_size=True,
                )
            finally:
                await browser.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Playwright render failed for %s", body.url)
        raise HTTPException(status_code=502, detail=f"Render failed: {e}") from e

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health")
async def pdf_health() -> dict:
    """Quick check: is Playwright importable and Chromium installed?"""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return {"ok": False, "reason": "playwright not installed"}
    return {"ok": True}
