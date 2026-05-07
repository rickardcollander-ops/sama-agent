"""
Brand Voice Scraper -- per-tenant tone extraction from customer websites.

Fetches the homepage + a handful of internal pages, extracts visible text,
and asks Claude to distill the brand's TONE, VOCABULARY, MESSAGING_PILLARS,
and writing rhythm. Persists the result to tenant_brand_voices keyed by
tenant_id so every subsequent generation for that tenant uses voice
extracted from THAT tenant's own pages.

Tenant isolation:
* tenant_id is required at every entry point (rejected when missing or
  'default').
* SELECT/INSERT/UPDATE on tenant_brand_voices is strictly keyed on tenant_id;
  there is no fallback to another tenant's voice.
* If a scrape fails, callers get an exception -- they do NOT silently
  receive another tenant's voice or a global Successifier default.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import httpx
from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)

TABLE = "tenant_brand_voices"
TTL_DAYS = 30
MAX_PAGES = 8
MAX_TEXT_PER_PAGE = 4000
DEFAULT_USER_AGENT = "SAMA-BrandVoiceScraper/1.0"

_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

# AI-tells the agent must avoid regardless of tenant -- merged into every
# extracted voice's vocabulary.avoid list.
_DEFAULT_AVOID_AI_TELLS: List[str] = [
    "—",  # em-dash
    "delve", "delving",
    "tapestry",
    "moreover", "furthermore",
    "in today's world", "in the realm of", "in the world of",
    "navigate", "navigating",
    "leverage",
    "harness",
    "robust",
    "seamless", "seamlessly",
    "game-changer", "revolutionary", "disruptive",
    "it's important to note",
    "in conclusion",
    "ever-evolving", "ever-changing",
    "unleash", "unlock",
    "elevate", "elevating",
    "foster",
]


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _HTML_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _normalize_domain(domain: str) -> str:
    if not domain:
        return ""
    domain = domain.strip()
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return domain.rstrip("/")


async def _fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=15)
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception as e:
        logger.debug(f"brand_voice_scraper: fetch failed for {url}: {e}")
        return None


def _internal_links(html: str, base_url: str) -> List[str]:
    base_host = urlparse(base_url).netloc
    out: List[str] = []
    seen: Set[str] = set()
    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url + "/", href)
        if urlparse(absolute).netloc != base_host:
            continue
        absolute = absolute.split("#")[0]
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


async def _crawl(domain: str, max_pages: int = MAX_PAGES) -> List[Dict[str, str]]:
    base = _normalize_domain(domain)
    if not base:
        return []

    pages: List[Dict[str, str]] = []
    visited: Set[str] = set()
    queue: List[str] = [base]

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(headers=headers) as client:
        while queue and len(pages) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            html = await _fetch(client, url)
            if not html:
                continue

            text = _strip_html(html)[:MAX_TEXT_PER_PAGE]
            if len(text) >= 200:
                pages.append({"url": url, "text": text})

            if len(pages) < max_pages:
                for link in _internal_links(html, url)[:20]:
                    if link not in visited:
                        queue.append(link)

    return pages


def _build_prompt(brand_name: str, domain: str, pages: List[Dict[str, str]]) -> str:
    samples = "\n\n---\n\n".join(
        f"URL: {p['url']}\n{p['text']}" for p in pages[:MAX_PAGES]
    )
    return f"""You are analysing the brand voice of a website to teach another writer to mimic it.

Brand: {brand_name or '(unknown)'}
Domain: {domain}

Below are the visible texts from {len(pages)} pages on the site. Read them and extract a structured profile of the writing style.

Return ONLY a JSON object (no markdown, no fences) with this exact shape:
{{
  "tone": {{
    "overall": "one paragraph describing the overall tone (formal/casual, expert/accessible, playful/sober, warm/clinical, etc.)",
    "do": ["bullet of what the writing DOES, e.g. 'short declarative sentences', 'addresses reader as you', 'opens with a question'"],
    "dont": ["bullet of what the writing AVOIDS, e.g. 'no exclamation points', 'no jargon', 'never uses em-dashes'"]
  }},
  "vocabulary": {{
    "preferred": {{"word_or_phrase": "how/why it is used"}},
    "avoid": ["clichés the brand stays away from"]
  }},
  "sentence_rhythm": {{
    "avg_sentence_length": "short|medium|long",
    "rhythm": "one sentence describing how sentences vary in length and structure"
  }},
  "messaging_pillars": [
    {{"title": "Pillar 1", "description": "one sentence", "key_phrases": ["distinctive phrase 1"]}},
    {{"title": "Pillar 2", "description": "one sentence", "key_phrases": ["..."]}},
    {{"title": "Pillar 3", "description": "one sentence", "key_phrases": ["..."]}}
  ],
  "proof_points": {{"label": "exact wording the brand uses"}},
  "target_persona": {{
    "title": "who they're writing to",
    "pain_points": ["pain 1", "pain 2"],
    "goals": ["goal 1", "goal 2"]
  }}
}}

Texts:

{samples}
"""


async def _extract_voice(brand_name: str, domain: str, pages: List[Dict[str, str]]) -> Dict[str, Any]:
    if not pages:
        raise ValueError("no pages to analyse")
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = _build_prompt(brand_name, domain, pages)

    def _call():
        return client.messages.create(
            model=getattr(settings, "CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

    response = await asyncio.to_thread(_call)
    text = response.content[0].text.strip()

    if text.startswith("```"):
        # Strip ``` or ```json fences
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        logger.error(f"brand_voice_scraper: JSON parse failed: {e}; raw: {text[:500]}")
        raise


def _merge_default_avoids(voice: Dict[str, Any]) -> Dict[str, Any]:
    vocab = voice.setdefault("vocabulary", {})
    avoid = vocab.setdefault("avoid", [])
    if not isinstance(avoid, list):
        avoid = []
    seen = {a.lower() for a in avoid if isinstance(a, str)}
    for term in _DEFAULT_AVOID_AI_TELLS:
        if term.lower() not in seen:
            avoid.append(term)
            seen.add(term.lower())
    vocab["avoid"] = avoid
    return voice


async def scrape_and_extract(
    tenant_id: str,
    domain: str,
    brand_name: str = "",
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Scrape `domain` and persist the extracted voice for `tenant_id`.

    Returns the voice_json dict. Strictly per-tenant: rejects empty or
    'default' tenant_id and never falls back to another tenant's voice.
    """
    if not tenant_id or tenant_id == "default":
        raise ValueError("tenant_id is required and must not be 'default'")
    if not domain:
        raise ValueError("domain is required")

    sb = get_supabase()

    if not force:
        try:
            existing = (
                sb.table(TABLE)
                .select("voice_json,scraped_at")
                .eq("tenant_id", tenant_id)
                .single()
                .execute()
            )
            if existing.data:
                scraped_at = existing.data.get("scraped_at")
                if scraped_at:
                    try:
                        scraped_dt = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - scraped_dt < timedelta(days=TTL_DAYS):
                            return existing.data["voice_json"]
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"brand_voice_scraper: existing lookup failed for {tenant_id}: {e}")

    logger.info(f"brand_voice_scraper: scraping domain={domain} for tenant={tenant_id}")

    pages = await _crawl(domain, max_pages=MAX_PAGES)
    if not pages:
        raise RuntimeError(f"could not fetch any pages from {domain}")

    voice = await _extract_voice(brand_name=brand_name, domain=domain, pages=pages)
    voice = _merge_default_avoids(voice)

    record = {
        "tenant_id": tenant_id,
        "voice_json": voice,
        "source_urls": [p["url"] for p in pages],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        sb.table(TABLE).upsert(record, on_conflict="tenant_id").execute()
        logger.info(
            f"brand_voice_scraper: persisted voice for tenant={tenant_id} from {len(pages)} pages"
        )
    except Exception as e:
        logger.error(f"brand_voice_scraper: persist failed for {tenant_id}: {e}")
        raise

    return voice


async def get_voice(tenant_id: str) -> Optional[Dict[str, Any]]:
    """Read the persisted voice for a tenant. Returns None if not yet scraped."""
    if not tenant_id or tenant_id == "default":
        return None
    try:
        sb = get_supabase()
        result = (
            sb.table(TABLE)
            .select("voice_json")
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )
        if result.data:
            return result.data.get("voice_json")
    except Exception as e:
        logger.debug(f"brand_voice_scraper.get_voice failed for {tenant_id}: {e}")
    return None
