"""
External page ingester.

Discovers URLs on the tenant's own site via sitemap.xml (with robots.txt
fallback), fetches lightweight metadata (title/description/H1) for each,
and persists them to the ``external_pages`` table together with an
embedding. The internal-linking optimizer then merges these rows with
the SAMA-authored ``content`` rows, so we can suggest links into pages
that exist outside our CMS (legacy blog, product pages, docs).

Sized for SMB sites: capped at 200 URLs per refresh, parallel HTTP
fetches, and a single Voyage batch for embeddings.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from shared.database import get_supabase, run_db
from shared.embeddings import embed_texts, DEFAULT_MODEL
from shared.safe_http import safe_get, UnsafeURLError

logger = logging.getLogger(__name__)

EXTERNAL_PAGES_TABLE = "external_pages"

DEFAULT_MAX_URLS = 200
SITEMAP_INDEX_DEPTH = 3
PAGE_FETCH_CONCURRENCY = 8
PAGE_FETCH_TIMEOUT_S = 10.0


def _normalize_url(url: str) -> str:
    """Strip fragments + trailing slash so we don't store dupes for
    /pricing and /pricing#features."""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


async def _fetch_text(url: str) -> Optional[str]:
    try:
        resp = await safe_get(url, timeout=PAGE_FETCH_TIMEOUT_S)
        if resp.status_code >= 400:
            return None
        return resp.text
    except (UnsafeURLError, httpx.HTTPError, ValueError) as e:
        logger.debug("fetch failed for %s: %s", url, e)
        return None


async def _discover_sitemap_urls(base_url: str, max_urls: int) -> List[str]:
    """Pull URLs from sitemap.xml plus any sitemap declared in robots.txt.
    Sitemap indexes are expanded recursively up to SITEMAP_INDEX_DEPTH."""
    base = base_url.rstrip("/")

    sitemap_candidates: List[str] = []
    robots = await _fetch_text(f"{base}/robots.txt")
    if robots:
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_candidates.append(line.split(":", 1)[1].strip())
    sitemap_candidates.append(f"{base}/sitemap.xml")

    seen_sitemaps: Set[str] = set()
    discovered: List[str] = []
    queue: List[Tuple[str, int]] = [(s, 0) for s in sitemap_candidates]

    while queue and len(discovered) < max_urls:
        sm_url, depth = queue.pop(0)
        if sm_url in seen_sitemaps or depth > SITEMAP_INDEX_DEPTH:
            continue
        seen_sitemaps.add(sm_url)

        body = await _fetch_text(sm_url)
        if not body:
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue

        # Strip XML namespace so the same code handles default + xhtml ns.
        tag = root.tag.split("}", 1)[-1]
        for loc in root.iter():
            loc_tag = loc.tag.split("}", 1)[-1]
            if loc_tag != "loc" or not loc.text:
                continue
            child = loc.text.strip()
            if tag == "sitemapindex":
                queue.append((child, depth + 1))
            else:
                discovered.append(_normalize_url(child))
                if len(discovered) >= max_urls:
                    break

    # De-dup while preserving order.
    seen: Set[str] = set()
    unique: List[str] = []
    for u in discovered:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


async def _scrape_metadata(url: str) -> Dict[str, Any]:
    """Fetch a page and extract title / meta description / first H1."""
    html = await _fetch_text(url)
    if not html:
        return {"url": url, "title": None, "description": None, "h1": None}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = desc_tag.get("content", "").strip() if desc_tag else None
    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(strip=True) if h1_tag else None
    return {"url": url, "title": title, "description": description, "h1": h1}


def _embedding_text(row: Dict[str, Any]) -> str:
    parts = [row.get("title") or "", row.get("h1") or "", row.get("description") or ""]
    return " | ".join(p for p in parts if p).strip() or row.get("url", "")


async def refresh_external_pages(
    base_url: str,
    *,
    tenant_id: str = "default",
    max_urls: int = DEFAULT_MAX_URLS,
) -> Dict[str, Any]:
    """Discover URLs on ``base_url``, scrape lightweight metadata, embed,
    and upsert into ``external_pages``. Safe to call repeatedly -- rows
    are upserted on (tenant_id, url) and ``last_seen_at`` is bumped."""
    if not base_url:
        return {"discovered": 0, "stored": 0, "tenant_id": tenant_id}

    urls = await _discover_sitemap_urls(base_url, max_urls)
    if not urls:
        logger.info("No URLs found in sitemap for %s", base_url)
        return {"discovered": 0, "stored": 0, "tenant_id": tenant_id}

    sem = asyncio.Semaphore(PAGE_FETCH_CONCURRENCY)

    async def _bounded(u: str) -> Dict[str, Any]:
        async with sem:
            return await _scrape_metadata(u)

    rows = await asyncio.gather(*[_bounded(u) for u in urls])

    embed_inputs = [_embedding_text(r) for r in rows]
    embeddings = await embed_texts(embed_inputs, input_type="document")
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = []
    for i, row in enumerate(rows):
        emb = embeddings[i] if embeddings else None
        payload.append(
            {
                "tenant_id": tenant_id,
                "url": row["url"],
                "title": row.get("title"),
                "description": row.get("description"),
                "h1": row.get("h1"),
                "embedding": emb,
                "embedding_model": DEFAULT_MODEL if emb else None,
                "last_seen_at": now_iso,
            }
        )

    sb = get_supabase()
    # Supabase upsert needs the unique constraint columns spelled out.
    await run_db(
        lambda: sb.table(EXTERNAL_PAGES_TABLE)
        .upsert(payload, on_conflict="tenant_id,url")
        .execute()
    )

    logger.info(
        "external_pages refresh: tenant=%s discovered=%d stored=%d embedded=%s",
        tenant_id,
        len(urls),
        len(payload),
        "yes" if embeddings else "no",
    )
    return {
        "discovered": len(urls),
        "stored": len(payload),
        "embedded": bool(embeddings),
        "tenant_id": tenant_id,
    }


async def list_external_pages(tenant_id: str = "default") -> List[Dict[str, Any]]:
    sb = get_supabase()
    result = await run_db(
        lambda: sb.table(EXTERNAL_PAGES_TABLE)
        .select("url,title,description,h1,embedding")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return result.data or []
