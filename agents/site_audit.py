"""
SiteAuditAgent — full-domain SEO + GEO + technical + link health audit.

Given a domain, this agent:
  1. Discovers pages via sitemap.xml (falls back to crawling the homepage).
  2. Fetches up to N pages and parses on-page signals with BeautifulSoup.
  3. HEAD-checks discovered links to find broken ones.
  4. Scores the site across five categories (technical / on-page / GEO /
     links / performance) plus an overall score.
  5. Produces structured findings + recommendations the dashboard can render
     as gauges, tables, and CTAs.

Network calls all go through a single httpx.AsyncClient so we share a
connection pool, follow redirects once, and respect a per-request timeout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from shared.tenant import TenantConfig

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────────
DEFAULT_MAX_PAGES = 15
DEFAULT_MAX_LINKS_TO_CHECK = 40
DEFAULT_TIMEOUT_S = 20.0
# Some CDNs (Cloudflare, Vercel edge) return 403 to "non-browser" UAs. We send
# a Chrome-like UA and identify ourselves in the comment so site owners can
# still allow-list us via robots.txt.
USER_AGENT = (
    "Mozilla/5.0 (compatible; SamaSiteAudit/1.0; +https://successifier.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class PageReport:
    url: str
    status_code: int
    response_ms: int
    title: Optional[str] = None
    title_length: int = 0
    meta_description: Optional[str] = None
    meta_description_length: int = 0
    h1_count: int = 0
    h2_count: int = 0
    h3_count: int = 0
    word_count: int = 0
    images_total: int = 0
    images_missing_alt: int = 0
    canonical: Optional[str] = None
    has_schema: bool = False
    schema_types: List[str] = field(default_factory=list)
    has_open_graph: bool = False
    has_viewport: bool = False
    has_lang: bool = False
    internal_links: int = 0
    external_links: int = 0
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class BrokenLink:
    url: str
    status_code: int
    found_on: List[str]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalise_domain(raw: str) -> Tuple[str, str]:
    """
    Return (base_url_with_scheme, hostname).
    'foo.com' → ('https://foo.com', 'foo.com')
    'https://foo.com/path' → ('https://foo.com', 'foo.com')
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("domain is empty")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"could not parse host from {raw!r}")
    base = f"{parsed.scheme}://{host}"
    if parsed.port:
        base += f":{parsed.port}"
    return base, host


def _abs_url(base: str, href: str) -> Optional[str]:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    try:
        joined = urljoin(base + "/", href)
        parsed = urlparse(joined)
        if parsed.scheme not in ("http", "https"):
            return None
        # Strip fragment
        return urlunparse(parsed._replace(fragment=""))
    except Exception:
        return None


def _toggle_www(url: str) -> Optional[str]:
    """Return the same URL with www. prepended or stripped. Used as a fallback
    when an apex domain doesn't redirect to www (or vice versa)."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return None
        if host.startswith("www."):
            new_host = host[4:]
        else:
            new_host = "www." + host
        netloc = new_host
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return None


def _is_same_host(url: str, host: str) -> bool:
    try:
        h = (urlparse(url).hostname or "").lower()
        host = host.lower()
        return h == host or h.endswith("." + host) or host.endswith("." + h)
    except Exception:
        return False


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


# ── The agent ────────────────────────────────────────────────────────────────

class SiteAuditAgent:
    def __init__(self, tenant_config: TenantConfig):
        self.tenant_config = tenant_config

    async def audit_domain(
        self,
        domain: str,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_links_to_check: int = DEFAULT_MAX_LINKS_TO_CHECK,
    ) -> Dict[str, Any]:
        """Run a full audit and return a JSON-serialisable report."""
        base_url, host = _normalise_domain(domain)
        started = time.time()

        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=10,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            verify=True,
        ) as client:
            robots, robots_info = await self._fetch_meta_file(client, base_url, "/robots.txt")
            llms, llms_info = await self._fetch_meta_file(client, base_url, "/llms.txt")
            sitemap_urls, sitemap_info = await self._discover_sitemap_urls(client, base_url, robots)
            logger.info(
                f"site-audit meta-files for {host}: "
                f"robots={robots_info} llms={llms_info} sitemap={sitemap_info}"
            )

            # Choose pages to audit: prefer sitemap, fall back to homepage links.
            pages_to_audit: List[str] = []
            if sitemap_urls:
                pages_to_audit = sitemap_urls[:max_pages]
            else:
                home_html, home_status, home_ms = await self._fetch_html(client, base_url + "/")
                if home_html:
                    home_links = self._extract_links(base_url, home_html, host, internal_only=True)
                    pages_to_audit = [base_url + "/"] + home_links[: max_pages - 1]
                else:
                    pages_to_audit = [base_url + "/"]

            # De-dupe and audit each page in parallel (bounded concurrency).
            seen: Set[str] = set()
            unique_pages = [p for p in pages_to_audit if not (p in seen or seen.add(p))]

            sem = asyncio.Semaphore(5)

            async def _bounded(url: str) -> Optional[PageReport]:
                async with sem:
                    return await self._audit_page(client, url, host)

            page_results = await asyncio.gather(*[_bounded(u) for u in unique_pages])
            pages: List[PageReport] = [p for p in page_results if p is not None]

            # Gather all unique outgoing links from audited pages and HEAD-check
            # a sample of them for broken-link detection.
            all_links: Dict[str, List[str]] = {}
            for page in pages:
                # We re-fetch just to extract hrefs; cheaper to re-parse the
                # cached HTML, but we kept the parsed page dict only. So skip:
                # links discovered during audit are tracked in `all_links` via
                # _audit_page hooks. For simplicity, do a second light pass.
                pass
            broken_links = await self._check_links(client, pages, base_url, host, max_links_to_check)

            # If no audited page actually loaded, the host is unreachable —
            # we cannot honestly score it. Returning fake "100" scores for
            # performance/link-health on an empty audit is misleading.
            successful_pages = [p for p in pages if p.status_code == 200]
            reachable = bool(successful_pages)
            meta_files_info = {
                "robots_txt": robots_info,
                "llms_txt": llms_info,
                "sitemap_xml": sitemap_info,
            }

            if not reachable:
                scores = {"overall": 0, "technical_seo": 0, "on_page_seo": 0,
                          "geo_readiness": 0, "link_health": 0, "performance": 0}
                findings = self._unreachable_findings(pages, base_url, meta_files_info)
                recommendations = self._compute_recommendations(findings)
            else:
                scores = self._compute_scores(successful_pages, robots, llms, sitemap_urls, base_url, broken_links)
                findings = self._compute_findings(pages, robots, llms, sitemap_urls, base_url, broken_links)
                recommendations = self._compute_recommendations(findings)

            summary = {
                "pages_analyzed": len(pages),
                "pages_loaded": len(successful_pages),
                "pages_failed": len(pages) - len(successful_pages),
                "reachable": reachable,
                "total_pages_discovered": len(sitemap_urls) if sitemap_urls else len(pages),
                "has_robots_txt": robots is not None,
                "has_sitemap_xml": bool(sitemap_urls),
                "has_llms_txt": llms is not None,
                "https": base_url.startswith("https://"),
                "avg_response_ms": (
                    int(sum(p.response_ms for p in successful_pages) / len(successful_pages))
                    if successful_pages else 0
                ),
                "total_links_checked": min(max_links_to_check, sum(p.internal_links + p.external_links for p in successful_pages)),
                "broken_links_count": len(broken_links),
                "audit_duration_ms": int((time.time() - started) * 1000),
                # Diagnostic detail so the dashboard can distinguish "file not
                # present" from "we couldn't reach the server" — the latter
                # should not be presented to users as a missing-file finding.
                "meta_files": meta_files_info,
            }

            return {
                "domain": host,
                "base_url": base_url,
                "scores": scores,
                "summary": summary,
                "pages": [p.to_dict() for p in pages],
                "broken_links": [bl.__dict__ for bl in broken_links],
                "findings": findings,
                "recommendations": recommendations,
            }

    # ── Network primitives ──────────────────────────────────────────────────

    async def _fetch_text(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        text, _status, _err = await self._fetch_text_detailed(client, url)
        return text

    async def _fetch_text_detailed(
        self, client: httpx.AsyncClient, url: str
    ) -> Tuple[Optional[str], int, Optional[str]]:
        """Fetch a text URL and report (text, status, error). Used so callers
        can distinguish "404 — file not present" from "network/timeout error".
        """
        try:
            r = await client.get(url)
        except httpx.TimeoutException as e:
            logger.info(f"timeout fetching {url}: {e}")
            return None, 0, "timeout"
        except httpx.HTTPError as e:
            logger.info(f"http error fetching {url}: {e}")
            return None, 0, type(e).__name__
        except Exception as e:
            logger.warning(f"unexpected error fetching {url}: {e}")
            return None, 0, type(e).__name__
        if r.status_code == 200 and r.text:
            return r.text, r.status_code, None
        return None, r.status_code, None

    async def _fetch_meta_file(
        self, client: httpx.AsyncClient, base_url: str, path: str
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """Fetch a well-known meta file (robots.txt / sitemap.xml / llms.txt),
        retrying once with the toggled apex↔www host if the first attempt
        looks like a network error or a redirect that wasn't followed.

        Returns (text_or_none, info_dict) where info_dict carries the final
        status and which URL won, so the API surfaces *why* a file looks
        missing instead of silently swallowing the failure.
        """
        primary = base_url.rstrip("/") + path
        text, status, err = await self._fetch_text_detailed(client, primary)
        info: Dict[str, Any] = {"url": primary, "status": status, "error": err}
        if text is not None:
            return text, info

        # If the first attempt failed at the network layer or returned a
        # redirect status (some hosts only do apex→www and httpx may not
        # have followed it for the root file), retry with the toggled host.
        retry_url = _toggle_www(primary)
        if retry_url and retry_url != primary and (
            err is not None or status in (301, 302, 307, 308) or status == 0
        ):
            text2, status2, err2 = await self._fetch_text_detailed(client, retry_url)
            info = {"url": retry_url, "status": status2, "error": err2,
                    "fallback_from": primary}
            if text2 is not None:
                return text2, info
        return None, info

    async def _fetch_html(
        self, client: httpx.AsyncClient, url: str
    ) -> Tuple[Optional[str], int, int]:
        start = time.time()
        try:
            r = await client.get(url)
            ms = int((time.time() - start) * 1000)
            ctype = r.headers.get("content-type", "").lower()
            if r.status_code < 400 and ("html" in ctype or not ctype):
                return r.text, r.status_code, ms
            return None, r.status_code, ms
        except Exception as e:
            logger.debug(f"fetch_html {url}: {e}")
            return None, 0, int((time.time() - start) * 1000)

    # ── Sitemap discovery ───────────────────────────────────────────────────

    async def _discover_sitemap_urls(
        self, client: httpx.AsyncClient, base_url: str, robots: Optional[str]
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Pull URLs from sitemap.xml (and robots.txt's Sitemap: directive).

        Returns (urls, info) so callers know which sitemap location worked
        and why it might have been empty.
        """
        sitemap_locations: List[str] = []

        # robots.txt may declare Sitemap: lines
        if robots:
            for line in robots.splitlines():
                if line.lower().startswith("sitemap:"):
                    loc = line.split(":", 1)[1].strip()
                    if loc:
                        sitemap_locations.append(loc)

        # Always probe the conventional location too — robots.txt may omit it.
        default_loc = base_url.rstrip("/") + "/sitemap.xml"
        if default_loc not in sitemap_locations:
            sitemap_locations.append(default_loc)

        urls: List[str] = []
        seen: Set[str] = set()
        attempts: List[Dict[str, Any]] = []
        for sm in sitemap_locations[:4]:  # don't follow forever
            # If the sitemap is at the apex domain, _fetch_meta_file gives us
            # the apex↔www fallback for free; otherwise just try once.
            if sm.endswith("/sitemap.xml"):
                parsed = urlparse(sm)
                sm_base = f"{parsed.scheme}://{parsed.netloc}"
                text, info = await self._fetch_meta_file(client, sm_base, "/sitemap.xml")
            else:
                text, status, err = await self._fetch_text_detailed(client, sm)
                info = {"url": sm, "status": status, "error": err}
            attempts.append(info)
            if not text:
                continue
            urls.extend(self._parse_sitemap(text, seen))
            if len(urls) > 200:
                break
        return urls, {"attempts": attempts}

    def _parse_sitemap(self, xml_text: str, seen: Set[str]) -> List[str]:
        """Parse a sitemap (or sitemap index) and return page URLs."""
        out: List[str] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return out
        # Strip namespace
        ns = re.match(r"\{(.*)\}", root.tag)
        nsmap = {"sm": ns.group(1)} if ns else {}
        loc_path = ".//sm:loc" if nsmap else ".//loc"
        for el in root.findall(loc_path, nsmap):
            url = (el.text or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    # ── Per-page audit ──────────────────────────────────────────────────────

    async def _audit_page(
        self, client: httpx.AsyncClient, url: str, host: str
    ) -> Optional[PageReport]:
        html, status, ms = await self._fetch_html(client, url)
        if html is None:
            return PageReport(url=url, status_code=status, response_ms=ms,
                              issues=["fetch_failed"])

        soup = BeautifulSoup(html, "lxml")
        report = PageReport(url=url, status_code=status, response_ms=ms)

        # Title
        if soup.title and soup.title.string:
            report.title = soup.title.string.strip()
            report.title_length = len(report.title)

        # Meta description
        md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if md and md.get("content"):
            report.meta_description = md["content"].strip()
            report.meta_description_length = len(report.meta_description)

        # Headings
        report.h1_count = len(soup.find_all("h1"))
        report.h2_count = len(soup.find_all("h2"))
        report.h3_count = len(soup.find_all("h3"))

        # Schema.org JSON-LD — extract BEFORE stripping <script> tags below.
        for s in soup.find_all("script", type=lambda v: v and "ld+json" in v.lower()):
            try:
                data = json.loads(s.string or "{}")
            except Exception:
                continue
            report.has_schema = True
            for t in self._extract_schema_types(data):
                if t and t not in report.schema_types:
                    report.schema_types.append(t)

        # Word count (rough — strip script/style after we've consumed them)
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        report.word_count = _word_count(soup.get_text(" ", strip=True))

        # Images
        imgs = soup.find_all("img")
        report.images_total = len(imgs)
        report.images_missing_alt = sum(
            1 for i in imgs if not (i.get("alt") and i["alt"].strip())
        )

        # Canonical
        canon = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
        if canon and canon.get("href"):
            report.canonical = canon["href"].strip()

        # Open Graph
        report.has_open_graph = bool(soup.find("meta", attrs={"property": re.compile(r"^og:", re.I)}))

        # Viewport
        vp = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
        report.has_viewport = bool(vp and vp.get("content"))

        # <html lang>
        html_tag = soup.find("html")
        report.has_lang = bool(html_tag and html_tag.get("lang"))

        # Links
        all_links = soup.find_all("a", href=True)
        for a in all_links:
            absu = _abs_url(url, a["href"])
            if not absu:
                continue
            if _is_same_host(absu, host):
                report.internal_links += 1
            else:
                report.external_links += 1

        # Per-page issues (used for findings rollup)
        if report.title_length == 0:
            report.issues.append("missing_title")
        elif report.title_length < 30:
            report.issues.append("short_title")
        elif report.title_length > 65:
            report.issues.append("long_title")
        if report.meta_description_length == 0:
            report.issues.append("missing_meta_description")
        elif report.meta_description_length < 80:
            report.issues.append("short_meta_description")
        elif report.meta_description_length > 165:
            report.issues.append("long_meta_description")
        if report.h1_count == 0:
            report.issues.append("missing_h1")
        elif report.h1_count > 1:
            report.issues.append("multiple_h1")
        if report.word_count < 300:
            report.issues.append("thin_content")
        if report.images_missing_alt > 0:
            report.issues.append("images_missing_alt")
        if not report.has_viewport:
            report.issues.append("missing_viewport")
        if not report.has_lang:
            report.issues.append("missing_html_lang")
        if not report.has_schema:
            report.issues.append("missing_structured_data")
        if not report.has_open_graph:
            report.issues.append("missing_open_graph")
        if not report.canonical:
            report.issues.append("missing_canonical")

        return report

    def _extract_schema_types(self, node: Any) -> List[str]:
        out: List[str] = []
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, list):
                out.extend([x for x in t if isinstance(x, str)])
            for v in node.values():
                out.extend(self._extract_schema_types(v))
        elif isinstance(node, list):
            for v in node:
                out.extend(self._extract_schema_types(v))
        return out

    # ── Link health ─────────────────────────────────────────────────────────

    def _extract_links(
        self, base: str, html: str, host: str, internal_only: bool = False
    ) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        out: List[str] = []
        seen: Set[str] = set()
        for a in soup.find_all("a", href=True):
            absu = _abs_url(base, a["href"])
            if not absu or absu in seen:
                continue
            if internal_only and not _is_same_host(absu, host):
                continue
            seen.add(absu)
            out.append(absu)
        return out

    async def _check_links(
        self,
        client: httpx.AsyncClient,
        pages: List[PageReport],
        base_url: str,
        host: str,
        max_links: int,
    ) -> List[BrokenLink]:
        """
        Re-fetch each audited page's HTML to enumerate <a href>s, then HEAD-
        check a sample for broken status. We keep this bounded to stay within
        a few seconds of total budget.
        """
        # Collect candidate links by re-fetching pages cheaply (HTML cache miss
        # is acceptable here — pages list is already small).
        link_to_sources: Dict[str, List[str]] = {}
        for page in pages[:10]:  # sample top-10 audited pages for link extraction
            text = await self._fetch_text(client, page.url)
            if not text:
                continue
            for href in self._extract_links(page.url, text, host, internal_only=False):
                link_to_sources.setdefault(href, []).append(page.url)
                if len(link_to_sources) >= max_links:
                    break
            if len(link_to_sources) >= max_links:
                break

        async def _head(url: str) -> Tuple[str, int]:
            try:
                # Try HEAD first; some servers reject it, fall back to GET.
                r = await client.head(url)
                if r.status_code in (405, 501) or r.status_code >= 400:
                    r = await client.get(url)
                return url, r.status_code
            except Exception:
                return url, 0

        sem = asyncio.Semaphore(8)

        async def _bounded(url: str) -> Tuple[str, int]:
            async with sem:
                return await _head(url)

        results = await asyncio.gather(*[_bounded(u) for u in list(link_to_sources.keys())[:max_links]])

        broken: List[BrokenLink] = []
        for url, status in results:
            if status == 0 or status >= 400:
                broken.append(BrokenLink(
                    url=url,
                    status_code=status,
                    found_on=link_to_sources.get(url, [])[:5],
                ))
        return broken

    # ── Scoring ─────────────────────────────────────────────────────────────

    def _compute_scores(
        self,
        pages: List[PageReport],
        robots: Optional[str],
        llms: Optional[str],
        sitemap_urls: List[str],
        base_url: str,
        broken_links: List[BrokenLink],
    ) -> Dict[str, int]:
        if not pages:
            return {"overall": 0, "technical_seo": 0, "on_page_seo": 0,
                    "geo_readiness": 0, "link_health": 0, "performance": 0}

        n = len(pages)

        # Technical SEO (0-100)
        tech = 100
        if not robots:
            tech -= 10
        if not sitemap_urls:
            tech -= 15
        if not base_url.startswith("https://"):
            tech -= 25
        viewport_pct = sum(1 for p in pages if p.has_viewport) / n
        lang_pct = sum(1 for p in pages if p.has_lang) / n
        canonical_pct = sum(1 for p in pages if p.canonical) / n
        tech -= int((1 - viewport_pct) * 15)
        tech -= int((1 - lang_pct) * 10)
        tech -= int((1 - canonical_pct) * 10)
        tech = max(0, min(100, tech))

        # On-page SEO (0-100)
        good_title = sum(1 for p in pages if 30 <= p.title_length <= 65) / n
        good_meta = sum(1 for p in pages if 80 <= p.meta_description_length <= 165) / n
        good_h1 = sum(1 for p in pages if p.h1_count == 1) / n
        good_word_count = sum(1 for p in pages if p.word_count >= 300) / n
        good_alt = sum(
            1 for p in pages
            if p.images_total == 0 or p.images_missing_alt / max(1, p.images_total) <= 0.1
        ) / n
        on_page = int((good_title * 25 + good_meta * 25 + good_h1 * 20
                       + good_word_count * 15 + good_alt * 15))

        # GEO readiness (0-100) — how friendly is the site for AI assistants
        geo = 0
        schema_pct = sum(1 for p in pages if p.has_schema) / n
        og_pct = sum(1 for p in pages if p.has_open_graph) / n
        meta_pct = sum(1 for p in pages if p.meta_description_length > 0) / n
        h2_pct = sum(1 for p in pages if p.h2_count >= 2) / n
        long_form_pct = sum(1 for p in pages if p.word_count >= 600) / n
        geo += int(schema_pct * 30)
        geo += int(og_pct * 15)
        geo += int(meta_pct * 15)
        geo += int(h2_pct * 15)
        geo += int(long_form_pct * 15)
        if llms:
            geo += 10
        geo = max(0, min(100, geo))

        # Link health (0-100) — penalise broken links
        total_checked = max(1, len(broken_links) + 20)  # baseline assumed sample
        broken_ratio = len(broken_links) / total_checked
        link_health = max(0, int(100 - broken_ratio * 200))
        if any("missing_internal_links" in p.issues for p in pages):
            link_health -= 5
        link_health = max(0, min(100, link_health))

        # Performance proxy (0-100) — based on response time (we have no Core
        # Web Vitals without a headless browser).
        avg_ms = sum(p.response_ms for p in pages) / n
        if avg_ms <= 200:
            perf = 100
        elif avg_ms <= 500:
            perf = 90
        elif avg_ms <= 1000:
            perf = 75
        elif avg_ms <= 2000:
            perf = 55
        elif avg_ms <= 4000:
            perf = 35
        else:
            perf = 15

        overall = int(round(
            tech * 0.25 + on_page * 0.25 + geo * 0.20 + link_health * 0.15 + perf * 0.15
        ))

        return {
            "overall": overall,
            "technical_seo": tech,
            "on_page_seo": on_page,
            "geo_readiness": geo,
            "link_health": link_health,
            "performance": perf,
        }

    # ── Findings + recommendations ──────────────────────────────────────────

    def _compute_findings(
        self,
        pages: List[PageReport],
        robots: Optional[str],
        llms: Optional[str],
        sitemap_urls: List[str],
        base_url: str,
        broken_links: List[BrokenLink],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        n = len(pages) or 1

        def _add(category: str, severity: str, title: str, description: str, affected: int) -> None:
            findings.append({
                "category": category,
                "severity": severity,
                "title": title,
                "description": description,
                "affected_pages": affected,
            })

        # Domain-level
        if not base_url.startswith("https://"):
            _add("technical", "critical", "Site not served over HTTPS",
                 "Search engines and browsers penalise non-HTTPS sites; switch to TLS.", n)
        if not robots:
            _add("technical", "warning", "robots.txt is missing",
                 "Add a robots.txt at the domain root to control crawler access.", 0)
        if not sitemap_urls:
            _add("technical", "warning", "sitemap.xml is missing",
                 "Submit a sitemap so search engines and AI crawlers can discover every page.", 0)
        if not llms:
            _add("geo", "info", "llms.txt not present",
                 "An llms.txt file lets you guide LLM crawlers about which content to prioritise.", 0)

        # Page-level rollups
        def _count(issue: str) -> int:
            return sum(1 for p in pages if issue in p.issues)

        rules = [
            ("missing_title",            "on_page",   "critical", "Pages missing a <title>",                  "Every page needs a unique, descriptive title between 30–65 characters."),
            ("short_title",              "on_page",   "warning",  "Page titles too short",                    "Aim for 30–65 characters to maximise SERP CTR."),
            ("long_title",               "on_page",   "warning",  "Page titles too long",                     "Titles over 65 chars get truncated in Google SERPs."),
            ("missing_meta_description", "on_page",   "warning",  "Pages missing a meta description",         "Meta descriptions help CTR and are surfaced by AI assistants summarising the page."),
            ("short_meta_description",   "on_page",   "info",     "Meta descriptions too short",              "Aim for 80–165 characters."),
            ("long_meta_description",    "on_page",   "info",     "Meta descriptions too long",               "Descriptions over 165 chars get truncated."),
            ("missing_h1",               "on_page",   "critical", "Pages missing an <h1>",                    "Every page needs exactly one h1 describing the page topic."),
            ("multiple_h1",              "on_page",   "warning",  "Pages with multiple <h1> tags",            "Use one h1 and structure the rest as h2/h3."),
            ("thin_content",             "on_page",   "warning",  "Thin content (<300 words)",                "Pages with under 300 words rarely rank or get cited by AI assistants."),
            ("images_missing_alt",       "on_page",   "warning",  "Images missing alt text",                  "Alt text helps accessibility, image search, and AI summarisation."),
            ("missing_viewport",         "technical", "critical", "Pages without mobile viewport",            "Add <meta name=\"viewport\"> for mobile rendering and Google mobile-friendly status."),
            ("missing_html_lang",        "technical", "warning",  "Pages without <html lang>",                "Set the lang attribute so search engines and AI know the language."),
            ("missing_structured_data",  "geo",       "warning",  "Pages without schema.org markup",          "Schema.org JSON-LD significantly boosts AI assistant citation rate."),
            ("missing_open_graph",       "geo",       "info",     "Pages without Open Graph tags",            "OG tags control how the page renders when shared and are read by some AI crawlers."),
            ("missing_canonical",        "technical", "info",     "Pages without canonical link",             "Canonical links prevent duplicate-content penalties."),
            ("fetch_failed",             "technical", "critical", "Pages that failed to load",                "These pages returned an error or timed out during the audit."),
        ]
        for issue, category, severity, title, desc in rules:
            c = _count(issue)
            if c > 0:
                _add(category, severity, title, desc, c)

        # Broken links
        if broken_links:
            _add("links", "critical" if len(broken_links) >= 5 else "warning",
                 f"{len(broken_links)} broken outbound link{'s' if len(broken_links) != 1 else ''}",
                 "Broken links waste crawl budget and damage user trust.", len(broken_links))

        # Healthy signals
        if all(p.has_schema for p in pages) and pages:
            _add("geo", "success", "All audited pages include structured data",
                 "Schema.org markup is consistently applied — keep it up to date.", n)
        if all(p.h1_count == 1 for p in pages) and pages:
            _add("on_page", "success", "All audited pages have exactly one <h1>",
                 "Heading structure is clean across the audited pages.", n)

        return findings

    def _unreachable_findings(
        self,
        pages: List[PageReport],
        base_url: str,
        meta_files: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Findings for the case where no audited page actually loaded.

        We deliberately suppress the usual "robots.txt missing" /
        "sitemap.xml missing" findings here — they're false negatives when
        the host itself was unreachable, and would otherwise drown out the
        real problem in the recommendations panel.
        """
        attempted = len(pages) or 1
        host = urlparse(base_url).hostname or base_url
        # Pull the most informative error code/string we can from meta-file
        # attempts to help the user (DNS error, timeout, 5xx, etc.).
        diag_bits: List[str] = []
        for label, info in (
            ("robots.txt", meta_files.get("robots_txt") or {}),
            ("sitemap.xml", meta_files.get("sitemap_xml") or {}),
            ("llms.txt", meta_files.get("llms_txt") or {}),
        ):
            attempts = info.get("attempts") if isinstance(info, dict) else None
            samples = attempts if isinstance(attempts, list) else [info]
            for s in samples or []:
                if not isinstance(s, dict):
                    continue
                err = s.get("error")
                status = s.get("status")
                if err:
                    diag_bits.append(f"{label}: {err}")
                    break
                if isinstance(status, int) and status >= 500:
                    diag_bits.append(f"{label}: HTTP {status}")
                    break
        diag = f" Diagnostic: {'; '.join(dict.fromkeys(diag_bits))}." if diag_bits else ""
        return [{
            "category": "technical",
            "severity": "critical",
            "title": f"Could not reach {host}",
            "description": (
                "Every audited URL failed to load, so we have no data to score "
                "this site. Check that the domain is correct and publicly "
                "reachable over HTTPS, then re-run the audit." + diag
            ),
            "affected_pages": attempted,
        }]

    def _compute_recommendations(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sev_priority = {"critical": "high", "warning": "medium", "info": "low", "success": "low"}
        # Drop "success" entries from recommendations.
        actionable = [f for f in findings if f["severity"] != "success"]
        # Sort: critical → warning → info, then by affected count descending.
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        actionable.sort(key=lambda f: (sev_order.get(f["severity"], 99), -f.get("affected_pages", 0)))
        return [
            {
                "priority": sev_priority.get(f["severity"], "low"),
                "category": f["category"],
                "title": f["title"],
                "description": f["description"],
                "affected_count": f.get("affected_pages", 0),
            }
            for f in actionable[:10]
        ]
