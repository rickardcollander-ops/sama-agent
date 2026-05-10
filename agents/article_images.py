"""
Hybrid image pipeline for premium articles.

  - Featured image: generated via OpenAI ``gpt-image-1`` (quality/size from
    ``settings``). Roughly $0.04 per call at quality=medium.
  - Inline section images: queried from Unsplash (free, attribution
    required). We pull the first relevant landscape photo per
    ``image_query`` and fall back to skipping the image when nothing
    matches or the API key is missing.

All network IO is wrapped in try/except — if a vendor is down or
unconfigured the writer keeps producing an article, just without that
image. Callers should treat ``None`` as "no image, render text only".
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from shared.config import settings


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Featured image — OpenAI gpt-image-1
# ──────────────────────────────────────────────────────────────────────────

_OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"


async def generate_featured_image(
    *,
    title: str,
    summary: str,
    primary_keyword: str = "",
) -> Optional[Dict[str, Any]]:
    """Generate a hero image. Returns ``{"data_url", "alt", "source"}`` or
    ``None`` when OpenAI isn't configured or the call fails.

    The result includes a base64 ``data_url`` rather than a hosted URL —
    callers (the writer) are responsible for uploading to Supabase Storage
    or another CDN if persistent hosting is needed. Returning the raw bytes
    here keeps this module storage-agnostic.
    """
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        logger.info("OPENAI_API_KEY not set — skipping featured image generation")
        return None

    prompt = (
        f"Editorial hero image for a B2B SaaS blog article titled \"{title}\". "
        f"Visualise: {summary}. "
        "Modern, photo-realistic, soft natural lighting, business setting, "
        "neutral muted palette, no on-image text or logos."
    )

    payload = {
        "model": settings.OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "size": settings.OPENAI_IMAGE_SIZE,
        "quality": settings.OPENAI_IMAGE_QUALITY,
        "n": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                _OPENAI_IMAGE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        res.raise_for_status()
        data = res.json()
        b64 = (data.get("data") or [{}])[0].get("b64_json")
        if not b64:
            logger.warning("OpenAI image response had no b64_json: %s", data)
            return None
        return {
            "data_url": f"data:image/png;base64,{b64}",
            "b64": b64,
            "alt": (primary_keyword or title)[:120],
            "source": "openai",
        }
    except httpx.HTTPStatusError as exc:
        logger.warning("OpenAI image API error %s: %s", exc.response.status_code, exc.response.text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Featured image generation failed: %s", exc)
    return None


# ──────────────────────────────────────────────────────────────────────────
# Inline images — Unsplash stock photos
# ──────────────────────────────────────────────────────────────────────────

_UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


async def fetch_stock_image(query: str) -> Optional[Dict[str, Any]]:
    """Pull one landscape stock photo for ``query`` from Unsplash.

    Returns ``{"url", "alt", "credit", "source"}`` or ``None``.
    Unsplash terms require attribution — credit string is exposed so the
    dashboard can render a small caption.
    """
    api_key = (settings.UNSPLASH_ACCESS_KEY or "").strip()
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                _UNSPLASH_SEARCH_URL,
                params={
                    "query": query,
                    "orientation": "landscape",
                    "per_page": 1,
                    "content_filter": "high",
                },
                headers={"Authorization": f"Client-ID {api_key}"},
            )
        res.raise_for_status()
        results = res.json().get("results") or []
        if not results:
            return None
        photo = results[0]
        urls = photo.get("urls") or {}
        user = photo.get("user") or {}
        return {
            "url": urls.get("regular") or urls.get("full") or urls.get("small"),
            "alt": photo.get("alt_description") or query,
            "credit": f"Photo by {user.get('name', 'Unsplash')} on Unsplash",
            "credit_url": (user.get("links") or {}).get("html"),
            "source": "unsplash",
        }
    except httpx.HTTPStatusError as exc:
        logger.warning("Unsplash API error %s: %s", exc.response.status_code, exc.response.text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stock image fetch failed for %r: %s", query, exc)
    return None


async def _generate_inline_image_openai(
    query: str,
    *,
    upload_path_prefix: str,
) -> Optional[Dict[str, Any]]:
    """Inline-image fallback when Unsplash isn't configured (or returns no
    match). Uses ``gpt-image-1`` like the featured pipe but with a section
    prompt and uploads to Supabase Storage so we don't bloat
    ``article_data`` with multiple base64 PNGs.
    """
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        return None

    prompt = (
        f"Editorial illustration for a B2B SaaS blog section about \"{query}\". "
        "Photo-realistic, modern business setting, soft natural lighting, "
        "neutral muted palette, no on-image text or logos."
    )
    payload = {
        "model": settings.OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "size": settings.OPENAI_IMAGE_SIZE,
        "quality": settings.OPENAI_IMAGE_QUALITY,
        "n": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                _OPENAI_IMAGE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        res.raise_for_status()
        b64 = (res.json().get("data") or [{}])[0].get("b64_json")
        if not b64:
            return None
    except httpx.HTTPStatusError as exc:
        logger.warning("OpenAI inline image error %s: %s", exc.response.status_code, exc.response.text[:200])
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI inline image failed for %r: %s", query, exc)
        return None

    digest = hashlib.md5(query.encode("utf-8")).hexdigest()[:10]
    ts = int(datetime.now(timezone.utc).timestamp())
    path = f"{upload_path_prefix.rstrip('/')}/inline-{digest}-{ts}.png"
    hosted = upload_featured_to_supabase(b64_png=b64, path=path)
    return {
        "url": hosted or f"data:image/png;base64,{b64}",
        "alt": query[:120],
        "source": "openai",
    }


async def _resolve_inline_image(
    query: str,
    *,
    upload_path_prefix: str,
) -> Optional[Dict[str, Any]]:
    """Try Unsplash first (free, attribution required); fall back to
    generating the image via OpenAI when Unsplash isn't configured or has
    no match. Either path returns the same dict shape so the writer can
    treat the result uniformly.
    """
    if (settings.UNSPLASH_ACCESS_KEY or "").strip():
        stock = await fetch_stock_image(query)
        if stock:
            return stock
    return await _generate_inline_image_openai(query, upload_path_prefix=upload_path_prefix)


async def fetch_inline_images(
    queries: List[str],
    *,
    upload_path_prefix: str = "inline",
) -> List[Optional[Dict[str, Any]]]:
    """Resolve a list of section image queries in parallel.

    Each query is resolved via Unsplash (preferred, free) with an OpenAI
    generation fallback so a missing Unsplash key still produces inline
    imagery — at the cost of one ``gpt-image-1`` call per slot.
    ``upload_path_prefix`` is used only by the OpenAI fallback to namespace
    Supabase Storage uploads (e.g. ``"<tenant>/<slug>"``).
    """
    if not queries:
        return []
    return await asyncio.gather(*(
        _resolve_inline_image(q, upload_path_prefix=upload_path_prefix) for q in queries
    ))


# ──────────────────────────────────────────────────────────────────────────
# Optional: persist a generated image to Supabase Storage
# ──────────────────────────────────────────────────────────────────────────


def upload_featured_to_supabase(
    *,
    b64_png: str,
    bucket: str = "article-images",
    path: str,
) -> Optional[str]:
    """Upload a base64 PNG to a public Supabase Storage bucket and return
    its public URL. Best-effort — returns ``None`` on failure so the writer
    can fall back to inlining the data URL in the JSON payload.
    """
    try:
        from shared.database import get_supabase
    except Exception:
        return None
    try:
        sb = get_supabase()
        sb.storage.from_(bucket).upload(
            path=path,
            file=base64.b64decode(b64_png),
            file_options={"content-type": "image/png", "upsert": "true"},
        )
        return sb.storage.from_(bucket).get_public_url(path)
    except Exception as exc:  # noqa: BLE001
        logger.info("Supabase Storage upload skipped (%s)", exc)
        return None
