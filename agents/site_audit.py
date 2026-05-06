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
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from shared.tenant import TenantConfig

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────────
# We default to 200 because most marketing sites publish 50–200 URLs in their
# sitemap and the user expects the audit to cover the whole site, not a sample.
# The API route enforces an absolute upper bound so a misconfigured giant site
# can't run for hours.
DEFAULT_MAX_PAGES = 200
# Hard ceiling on how many URLs we expand out of (possibly nested) sitemaps.
# Protects against runaway sitemap indexes; well above any realistic SMB site.
SITEMAP_DISCOVERY_CAP = 1000
# How deep we recurse into sitemap indexes before giving up. Real-world sites
# nest at most 1–2 levels (index → per-type → per-page); 3 leaves headroom.
SITEMAP_MAX_DEPTH = 3
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
    images_missing_dimensions: int = 0
    images_missing_lazy: int = 0
    canonical: Optional[str] = None
    has_schema: bool = False
    schema_types: List[str] = field(default_factory=list)
    has_open_graph: bool = False
    has_viewport: bool = False
    has_lang: bool = False
    has_hreflang: bool = False
    internal_links: int = 0
    external_links: int = 0
    # Body keywords per page so the keyword opportunity layer can build
    # site-wide topic clusters without re-fetching.
    h1_text: Optional[str] = None
    h2_texts: List[str] = field(default_factory=list)
    body_text_sample: Optional[str] = None
    # Security headers captured from the HTTP response — used for the
    # technical-SEO score and to surface concrete fix instructions.
    security_headers: Dict[str, bool] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        # body_text_sample is captured for keyword extraction but is too
        # noisy to ship over the wire; strip it from the page payload.
        d = self.__dict__.copy()
        d.pop("body_text_sample", None)
        return d


@dataclass
class BrokenLink:
    url: str
    status_code: int
    found_on: List[str]


# Security headers we surface in the audit. Keys are the response-header names
# (case-insensitive lookup), values are short labels and the fix snippet shown
# to users when missing.
SECURITY_HEADERS: List[Tuple[str, str, str]] = [
    ("strict-transport-security",
     "HSTS",
     'Add `Strict-Transport-Security: max-age=31536000; includeSubDomains` to force HTTPS.'),
    ("content-security-policy",
     "CSP",
     'Add a `Content-Security-Policy` header restricting script/style sources.'),
    ("x-content-type-options",
     "X-Content-Type-Options",
     'Add `X-Content-Type-Options: nosniff` to block MIME-sniffing attacks.'),
    ("referrer-policy",
     "Referrer-Policy",
     'Add `Referrer-Policy: strict-origin-when-cross-origin` to limit referrer leakage.'),
    ("x-frame-options",
     "X-Frame-Options",
     'Add `X-Frame-Options: SAMEORIGIN` (or use CSP `frame-ancestors`) to prevent clickjacking.'),
]


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
        progress_cb: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Run a full audit and return a JSON-serialisable report.

        ``progress_cb`` is invoked as ``await cb(pages_done, pages_total)``
        once after sitemap discovery (with ``done=0``) and again after each
        page completes, so the dashboard's running-jobs widget can show a
        real progress bar instead of a time-based estimate.
        """
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
                home_html, home_status, home_ms, _home_headers = await self._fetch_html(client, base_url + "/")
                if home_html:
                    home_links = self._extract_links(base_url, home_html, host, internal_only=True)
                    pages_to_audit = [base_url + "/"] + home_links[: max_pages - 1]
                else:
                    pages_to_audit = [base_url + "/"]

            # De-dupe and audit each page in parallel (bounded concurrency).
            seen: Set[str] = set()
            unique_pages = [p for p in pages_to_audit if not (p in seen or seen.add(p))]

            total = len(unique_pages)
            if progress_cb:
                try:
                    await progress_cb(0, total)
                except Exception:
                    logger.debug("progress_cb failed at start", exc_info=True)

            sem = asyncio.Semaphore(5)
            done_counter = 0
            done_lock = asyncio.Lock()

            async def _bounded(url: str) -> Optional[PageReport]:
                nonlocal done_counter
                async with sem:
                    result = await self._audit_page(client, url, host)
                if progress_cb:
                    async with done_lock:
                        done_counter += 1
                        snapshot = done_counter
                    try:
                        await progress_cb(snapshot, total)
                    except Exception:
                        logger.debug("progress_cb failed mid-run", exc_info=True)
                return result

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

            # Group recommendations by execution profile so the dashboard
            # can render quick-wins / strategic / technical-debt buckets
            # instead of a flat top-N list.
            recommendation_groups = self._group_recommendations(recommendations)

            # Keyword opportunities — best-effort. We pass the already-crawled
            # pages so the keyword agent doesn't refetch the site.
            keyword_opportunities = await self._build_keyword_opportunities(
                client, host, base_url, successful_pages
            )

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
                "recommendation_groups": recommendation_groups,
                "keyword_opportunities": keyword_opportunities,
            }

    # ── Recommendation grouping + keyword opportunities ─────────────────────

    @staticmethod
    def _group_recommendations(recs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Bucket recommendations by their `group` field. A recommendation
        with no group is treated as technical-debt by default."""
        groups: Dict[str, List[Dict[str, Any]]] = {
            "quick_win": [],
            "strategic": [],
            "technical_debt": [],
            "monitoring": [],
        }
        for r in recs:
            g = r.get("group") or "technical_debt"
            groups.setdefault(g, []).append(r)
        return groups

    async def _build_keyword_opportunities(
        self,
        client: httpx.AsyncClient,
        host: str,
        base_url: str,
        pages: List[PageReport],
    ) -> Optional[Dict[str, Any]]:
        """Run the keyword opportunity agent against the audited pages.

        Returns None when there's no data to analyse or the agent fails — the
        dashboard treats a missing field as "feature unavailable" rather than
        an error, so this stays best-effort.
        """
        if not pages:
            return None
        try:
            from agents.keyword_opportunity import KeywordOpportunityAgent
        except Exception as e:
            logger.info(f"keyword_opportunity import skipped: {e}")
            return None
        try:
            agent = KeywordOpportunityAgent(
                tenant_config=getattr(self, "tenant_config", None)
            )
            return await agent.build(
                host=host, base_url=base_url, pages=pages, client=client,
            )
        except Exception as e:
            logger.warning(f"keyword opportunities failed for {host}: {e}")
            return None

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
    ) -> Tuple[Optional[str], int, int, Dict[str, str]]:
        """Returns (html, status, ms, response_headers).

        Headers are returned even on non-HTML or error responses so callers can
        still inspect things like security headers when a page is reachable
        but renders no body (e.g. a CDN edge response).
        """
        start = time.time()
        try:
            r = await client.get(url)
            ms = int((time.time() - start) * 1000)
            headers = {k.lower(): v for k, v in r.headers.items()}
            ctype = headers.get("content-type", "").lower()
            if r.status_code < 400 and ("html" in ctype or not ctype):
                return r.text, r.status_code, ms, headers
            return None, r.status_code, ms, headers
        except Exception as e:
            logger.debug(f"fetch_html {url}: {e}")
            return None, 0, int((time.time() - start) * 1000), {}

    # ── Sitemap discovery ───────────────────────────────────────────────────

    async def _discover_sitemap_urls(
        self, client: httpx.AsyncClient, base_url: str, robots: Optional[str]
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Pull URLs from sitemap.xml (and robots.txt's Sitemap: directive),
        recursively expanding sitemap indexes into their child sitemaps.

        Returns (urls, info) so callers know which sitemap location(s) worked
        and why they might have been empty.
        """
        seed_locations: List[str] = []

        # robots.txt may declare Sitemap: lines
        if robots:
            for line in robots.splitlines():
                if line.lower().startswith("sitemap:"):
                    loc = line.split(":", 1)[1].strip()
                    if loc:
                        seed_locations.append(loc)

        # Always probe the conventional location too — robots.txt may omit it.
        default_loc = base_url.rstrip("/") + "/sitemap.xml"
        if default_loc not in seed_locations:
            seed_locations.append(default_loc)

        page_urls: List[str] = []
        seen_pages: Set[str] = set()
        seen_sitemaps: Set[str] = set()
        attempts: List[Dict[str, Any]] = []

        # BFS over (sitemap_url, depth). We need BFS rather than the previous
        # single-pass loop because a "sitemap.xml" can be a <sitemapindex>
        # listing other sitemaps (which Yoast and most CMSes generate by
        # default). The old code treated those child <loc> entries as page
        # URLs, which is why onlinesverige.se reported "2 pages analyzed" —
        # the two children of the index were crawled as if they were pages.
        queue: List[Tuple[str, int]] = [(loc, 0) for loc in seed_locations[:4]]

        while queue and len(page_urls) < SITEMAP_DISCOVERY_CAP:
            sm, depth = queue.pop(0)
            if sm in seen_sitemaps:
                continue
            seen_sitemaps.add(sm)

            # Use the apex↔www fallback only for the conventional path; nested
            # sitemap URLs are explicit enough to fetch directly.
            if sm.rstrip("/").endswith("/sitemap.xml") and depth == 0:
                parsed = urlparse(sm)
                sm_base = f"{parsed.scheme}://{parsed.netloc}"
                text, info = await self._fetch_meta_file(client, sm_base, "/sitemap.xml")
            else:
                text, status, err = await self._fetch_text_detailed(client, sm)
                info = {"url": sm, "status": status, "error": err}
            info["depth"] = depth
            attempts.append(info)
            if not text:
                continue

            new_pages, child_sitemaps = self._parse_sitemap(text, seen_pages)
            page_urls.extend(new_pages)

            if depth + 1 < SITEMAP_MAX_DEPTH:
                for child in child_sitemaps:
                    if child not in seen_sitemaps:
                        queue.append((child, depth + 1))

        # Trim to discovery cap without dropping the discovery work we did.
        if len(page_urls) > SITEMAP_DISCOVERY_CAP:
            page_urls = page_urls[:SITEMAP_DISCOVERY_CAP]

        return page_urls, {"attempts": attempts, "discovered": len(page_urls)}

    def _parse_sitemap(
        self, xml_text: str, seen_pages: Set[str]
    ) -> Tuple[List[str], List[str]]:
        """Parse a single sitemap document.

        Returns (page_urls, child_sitemap_urls). A `<urlset>` document yields
        page URLs; a `<sitemapindex>` yields child sitemap URLs to fetch
        next. Treating the two cases identically (as the old implementation
        did) caused index files to be crawled as if their children were pages.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return [], []

        ns_match = re.match(r"\{(.*)\}", root.tag)
        nsmap = {"sm": ns_match.group(1)} if ns_match else {}
        loc_path = ".//sm:loc" if nsmap else ".//loc"
        # Strip namespace from the local tag name for the index check.
        local_tag = root.tag.split("}", 1)[-1].lower()
        is_index = local_tag == "sitemapindex"

        locs: List[str] = []
        for el in root.findall(loc_path, nsmap):
            url = (el.text or "").strip()
            if url:
                locs.append(url)

        if is_index:
            return [], locs

        # Regular <urlset>: dedupe against pages we've already collected.
        page_urls: List[str] = []
        for url in locs:
            if url in seen_pages:
                continue
            seen_pages.add(url)
            page_urls.append(url)
        return page_urls, []

    # ── Per-page audit ──────────────────────────────────────────────────────

    async def _audit_page(
        self, client: httpx.AsyncClient, url: str, host: str
    ) -> Optional[PageReport]:
        html, status, ms, headers = await self._fetch_html(client, url)
        sec_headers = self._inspect_security_headers(headers)
        if html is None:
            return PageReport(url=url, status_code=status, response_ms=ms,
                              security_headers=sec_headers,
                              issues=["fetch_failed"])

        soup = BeautifulSoup(html, "lxml")
        report = PageReport(url=url, status_code=status, response_ms=ms,
                            security_headers=sec_headers)

        # Title
        if soup.title and soup.title.string:
            report.title = soup.title.string.strip()
            report.title_length = len(report.title)

        # Meta description
        md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if md and md.get("content"):
            report.meta_description = md["content"].strip()
            report.meta_description_length = len(report.meta_description)

        # Headings — also keep the actual H1/H2 text so the keyword
        # opportunity layer can extract topic targeting per page.
        h1_tags = soup.find_all("h1")
        h2_tags = soup.find_all("h2")
        report.h1_count = len(h1_tags)
        report.h2_count = len(h2_tags)
        report.h3_count = len(soup.find_all("h3"))
        if h1_tags:
            report.h1_text = (h1_tags[0].get_text(" ", strip=True) or None)
        report.h2_texts = [
            (h.get_text(" ", strip=True) or "")
            for h in h2_tags[:10] if h.get_text(strip=True)
        ]

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

        # Word count + body sample (rough — strip script/style first).
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        body_text = soup.get_text(" ", strip=True)
        report.word_count = _word_count(body_text)
        # Keep first ~6k chars for keyword extraction; full body text would
        # blow up the JSON payload but a sample captures the page's topic.
        report.body_text_sample = body_text[:6000] if body_text else None

        # Images — alt, dimensions (CLS), and lazy-loading.
        imgs = soup.find_all("img")
        report.images_total = len(imgs)
        report.images_missing_alt = sum(
            1 for i in imgs if not (i.get("alt") and i["alt"].strip())
        )
        report.images_missing_dimensions = sum(
            1 for i in imgs if not (i.get("width") and i.get("height"))
        )
        # Only flag missing lazy-loading when the page has enough images that
        # below-the-fold lazy-loading actually matters. The first ~3 images
        # are typically above the fold, so we exclude them.
        if len(imgs) > 3:
            report.images_missing_lazy = sum(
                1 for i in imgs[3:]
                if (i.get("loading") or "").strip().lower() != "lazy"
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

        # Hreflang — important for international SEO. We only require it on
        # sites that actually have multilingual variants, but we still record
        # presence so the site-level finding can decide whether to flag it.
        report.has_hreflang = bool(soup.find("link", rel=lambda v: v and "alternate" in (v if isinstance(v, list) else [v]),
                                              hreflang=True))

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
        if report.images_missing_dimensions > 0 and report.images_total > 0:
            report.issues.append("images_missing_dimensions")
        if report.images_missing_lazy > 0:
            report.issues.append("images_missing_lazy")
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
        # Security headers — only flag the most impactful ones at the page
        # level; the site-level rollup will summarise the rest.
        if not report.security_headers.get("strict-transport-security", False):
            report.issues.append("missing_hsts")
        if not report.security_headers.get("x-content-type-options", False):
            report.issues.append("missing_xcto")

        return report

    def _inspect_security_headers(self, headers: Dict[str, str]) -> Dict[str, bool]:
        """Returns a presence map for the security headers we surface."""
        return {name: bool(headers.get(name)) for name, _, _ in SECURITY_HEADERS}

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

    # Single source of truth for page-level findings. Each entry is keyed by
    # the issue string emitted from `_audit_page` and carries enough context
    # to produce both a finding and an actionable recommendation.
    @staticmethod
    def _format_issue_detail(issue: str, page: "PageReport") -> str:
        """Render a short, page-specific snippet for a single issue so the
        dashboard can show e.g. `'Hem' — 3 chars` next to the URL instead of
        just the URL. Empty string when there's nothing useful to show."""
        title = (page.title or "").strip()
        snippet = title if len(title) <= 50 else title[:47] + "…"
        if issue == "missing_title":
            return "no <title>"
        if issue in ("short_title", "long_title"):
            return f'"{snippet}" — {page.title_length} chars' if title else f"{page.title_length} chars"
        if issue == "missing_meta_description":
            return "no meta description"
        if issue in ("short_meta_description", "long_meta_description"):
            return f"{page.meta_description_length} chars"
        if issue == "missing_h1":
            return "no <h1>"
        if issue == "multiple_h1":
            return f"{page.h1_count} <h1> tags"
        if issue == "thin_content":
            return f"{page.word_count} words"
        if issue == "images_missing_alt":
            return f"{page.images_missing_alt}/{page.images_total} images"
        if issue == "images_missing_dimensions":
            return f"{page.images_missing_dimensions}/{page.images_total} images"
        if issue == "images_missing_lazy":
            return f"{page.images_missing_lazy}/{page.images_total} images"
        if issue == "fetch_failed":
            return f"HTTP {page.status_code or 'error'}"
        return ""

    PAGE_ISSUE_RULES: List[Tuple[str, Dict[str, Any]]] = [
        ("missing_title", {
            "category": "on_page", "severity": "critical",
            "title": "Pages missing a <title>",
            "description": "Every page needs a unique, descriptive title between 30–65 characters.",
            "how_to_fix": "Add `<title>Primary keyword | Brand</title>` in each page's <head>. Lead with the keyword, end with the brand.",
            "impact": "high", "effort": "low", "group": "quick_win",
        }),
        ("short_title", {
            "category": "on_page", "severity": "warning",
            "title": "Page titles too short",
            "description": "Aim for 30–65 characters to maximise SERP CTR.",
            "how_to_fix": "Expand each title with a benefit or qualifier (location, year, audience). Keep total length 30–65 chars.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("long_title", {
            "category": "on_page", "severity": "warning",
            "title": "Page titles too long",
            "description": "Titles over 65 chars get truncated in Google SERPs.",
            "how_to_fix": "Trim filler words and move secondary modifiers to the meta description.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("missing_meta_description", {
            "category": "on_page", "severity": "warning",
            "title": "Pages missing a meta description",
            "description": "Meta descriptions help CTR and are surfaced by AI assistants summarising the page.",
            "how_to_fix": "Add `<meta name=\"description\" content=\"…\">` summarising the page in 80–165 chars with a clear benefit.",
            "impact": "high", "effort": "low", "group": "quick_win",
        }),
        ("short_meta_description", {
            "category": "on_page", "severity": "info",
            "title": "Meta descriptions too short",
            "description": "Aim for 80–165 characters.",
            "how_to_fix": "Expand the description with a concrete benefit or differentiator.",
            "impact": "low", "effort": "low", "group": "quick_win",
        }),
        ("long_meta_description", {
            "category": "on_page", "severity": "info",
            "title": "Meta descriptions too long",
            "description": "Descriptions over 165 chars get truncated.",
            "how_to_fix": "Tighten copy; keep the value-prop in the first 120 chars so it survives truncation.",
            "impact": "low", "effort": "low", "group": "quick_win",
        }),
        ("missing_h1", {
            "category": "on_page", "severity": "critical",
            "title": "Pages missing an <h1>",
            "description": "Every page needs exactly one h1 describing the page topic.",
            "how_to_fix": "Add a single `<h1>` near the top of the main content with the primary keyword.",
            "impact": "high", "effort": "low", "group": "quick_win",
        }),
        ("multiple_h1", {
            "category": "on_page", "severity": "warning",
            "title": "Pages with multiple <h1> tags",
            "description": "Use one h1 and structure the rest as h2/h3.",
            "how_to_fix": "Keep one `<h1>` and convert duplicates to `<h2>` / `<h3>`.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("thin_content", {
            "category": "on_page", "severity": "warning",
            "title": "Thin content (<300 words)",
            "description": "Pages with under 300 words rarely rank or get cited by AI assistants.",
            "how_to_fix": "Expand to 600+ words with FAQs, examples, comparisons, and original data.",
            "impact": "high", "effort": "high", "group": "strategic",
        }),
        ("images_missing_alt", {
            "category": "on_page", "severity": "warning",
            "title": "Images missing alt text",
            "description": "Alt text helps accessibility, image search, and AI summarisation.",
            "how_to_fix": "Add descriptive `alt=\"…\"` to each `<img>`. Decorative images can use `alt=\"\"`.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("images_missing_dimensions", {
            "category": "performance", "severity": "warning",
            "title": "Images missing width/height",
            "description": "Without explicit dimensions the browser can't reserve space, hurting Cumulative Layout Shift (CLS).",
            "how_to_fix": "Add `width` and `height` attributes (or set `aspect-ratio` in CSS) on every `<img>`.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("images_missing_lazy", {
            "category": "performance", "severity": "info",
            "title": "Below-the-fold images not lazy-loaded",
            "description": "Lazy-loading defers off-screen images and improves Largest Contentful Paint (LCP).",
            "how_to_fix": "Add `loading=\"lazy\"` to images that appear below the initial viewport.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("missing_viewport", {
            "category": "technical", "severity": "critical",
            "title": "Pages without mobile viewport",
            "description": "Without a viewport meta tag Google's mobile-first index treats the page as non-mobile-friendly.",
            "how_to_fix": "Add `<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">` to every page.",
            "impact": "high", "effort": "low", "group": "quick_win",
        }),
        ("missing_html_lang", {
            "category": "technical", "severity": "warning",
            "title": "Pages without <html lang>",
            "description": "Set the lang attribute so search engines and AI know the language.",
            "how_to_fix": "Set `<html lang=\"sv\">` (or appropriate ISO code) on every page.",
            "impact": "low", "effort": "low", "group": "quick_win",
        }),
        ("missing_structured_data", {
            "category": "geo", "severity": "warning",
            "title": "Pages without schema.org markup",
            "description": "Schema.org JSON-LD significantly boosts AI assistant citation rate and SERP rich results.",
            "how_to_fix": "Add JSON-LD for `Organization`, `WebSite`, and the page-specific type (Article, Product, FAQPage, HowTo).",
            "impact": "high", "effort": "medium", "group": "strategic",
        }),
        ("missing_open_graph", {
            "category": "geo", "severity": "info",
            "title": "Pages without Open Graph tags",
            "description": "OG tags control how the page renders when shared and are read by some AI crawlers.",
            "how_to_fix": "Add `og:title`, `og:description`, `og:image`, and `og:url` meta tags in each page's <head>.",
            "impact": "medium", "effort": "low", "group": "quick_win",
        }),
        ("missing_canonical", {
            "category": "technical", "severity": "info",
            "title": "Pages without canonical link",
            "description": "Canonical links prevent duplicate-content penalties.",
            "how_to_fix": "Add `<link rel=\"canonical\" href=\"<absolute-url>\">` on every page (self-referential is fine).",
            "impact": "low", "effort": "low", "group": "quick_win",
        }),
        ("missing_hsts", {
            "category": "technical", "severity": "warning",
            "title": "HTTPS not enforced (HSTS missing)",
            "description": "Without HSTS, browsers may downgrade requests to HTTP on first visit.",
            "how_to_fix": "Send `Strict-Transport-Security: max-age=31536000; includeSubDomains` from the server.",
            "impact": "medium", "effort": "low", "group": "technical_debt",
        }),
        ("missing_xcto", {
            "category": "technical", "severity": "info",
            "title": "X-Content-Type-Options missing",
            "description": "MIME-sniffing protection is recommended by browsers and security scanners.",
            "how_to_fix": "Send `X-Content-Type-Options: nosniff` from the server.",
            "impact": "low", "effort": "low", "group": "technical_debt",
        }),
        ("fetch_failed", {
            "category": "technical", "severity": "critical",
            "title": "Pages that failed to load",
            "description": "These pages returned an error or timed out during the audit.",
            "how_to_fix": "Check server logs for errors; verify the URLs aren't blocked by robots.txt or a CDN rule.",
            "impact": "high", "effort": "medium", "group": "technical_debt",
        }),
    ]

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

        def _add(
            category: str, severity: str, title: str, description: str,
            affected: int, *,
            affected_urls: Optional[List[str]] = None,
            examples: Optional[List[Dict[str, str]]] = None,
            how_to_fix: Optional[str] = None,
            impact: Optional[str] = None,
            effort: Optional[str] = None,
            group: Optional[str] = None,
        ) -> None:
            findings.append({
                "category": category,
                "severity": severity,
                "title": title,
                "description": description,
                "affected_pages": affected,
                "affected_urls": (affected_urls or [])[:8],
                # Concrete page → value pairs so the dashboard can show
                # "Hem (3 chars)" next to each URL instead of just a list of
                # bare URLs. Capped to keep payload small.
                "examples": (examples or [])[:8],
                "how_to_fix": how_to_fix,
                "impact": impact,
                "effort": effort,
                "group": group,
            })

        # Domain-level — these don't have specific URLs but still benefit
        # from concrete fix instructions.
        if not base_url.startswith("https://"):
            _add("technical", "critical", "Site not served over HTTPS",
                 "Search engines and browsers penalise non-HTTPS sites; switch to TLS.", n,
                 how_to_fix="Provision a TLS certificate (Let's Encrypt is free) and redirect all HTTP traffic to HTTPS.",
                 impact="high", effort="medium", group="technical_debt")
        if not robots:
            _add("technical", "warning", "robots.txt is missing",
                 "Add a robots.txt at the domain root to control crawler access.", 0,
                 how_to_fix="Create `/robots.txt` allowing crawlers and pointing at your sitemap: `Sitemap: https://example.com/sitemap.xml`.",
                 impact="medium", effort="low", group="quick_win")
        if not sitemap_urls:
            _add("technical", "warning", "sitemap.xml is missing",
                 "Submit a sitemap so search engines and AI crawlers can discover every page.", 0,
                 how_to_fix="Generate `/sitemap.xml`, reference it in `robots.txt`, and submit it in Google Search Console.",
                 impact="high", effort="low", group="quick_win")
        if not llms:
            _add("geo", "info", "llms.txt not present",
                 "An llms.txt file lets you guide LLM crawlers about which content to prioritise.", 0,
                 how_to_fix="Create `/llms.txt` with a concise list of your most-cite-worthy URLs and what each covers.",
                 impact="medium", effort="low", group="quick_win")

        # Site-level: duplicate titles / meta descriptions across audited pages.
        title_to_urls: Dict[str, List[str]] = {}
        meta_to_urls: Dict[str, List[str]] = {}
        for p in pages:
            if p.title and p.title.strip():
                title_to_urls.setdefault(p.title.strip(), []).append(p.url)
            if p.meta_description and p.meta_description.strip():
                meta_to_urls.setdefault(p.meta_description.strip(), []).append(p.url)
        dupe_title_urls = [u for urls in title_to_urls.values() if len(urls) > 1 for u in urls]
        dupe_meta_urls = [u for urls in meta_to_urls.values() if len(urls) > 1 for u in urls]
        if dupe_title_urls:
            _add("on_page", "warning", "Duplicate page titles",
                 "Multiple pages share the same <title>, diluting topical relevance and SERP CTR.",
                 len(dupe_title_urls),
                 affected_urls=dupe_title_urls,
                 how_to_fix="Make each page's <title> unique by adding a page-specific qualifier (topic, location, model, year).",
                 impact="medium", effort="low", group="quick_win")
        if dupe_meta_urls:
            _add("on_page", "info", "Duplicate meta descriptions",
                 "Multiple pages share the same meta description, weakening their SERP appearance.",
                 len(dupe_meta_urls),
                 affected_urls=dupe_meta_urls,
                 how_to_fix="Write a unique meta description for each page summarising its specific value.",
                 impact="low", effort="low", group="quick_win")

        # Site-level: hreflang. Only flag if NO page has it — single-language
        # sites are a valid configuration.
        if pages and not any(p.has_hreflang for p in pages):
            _add("technical", "info", "No hreflang tags found",
                 "If you serve multiple languages or regions, hreflang tells Google which version to show.",
                 0,
                 how_to_fix="Add `<link rel=\"alternate\" hreflang=\"<code>\" href=\"<url>\">` for each language/region variant in <head>.",
                 impact="medium", effort="medium", group="strategic")

        # Site-level: security headers (most useful when consistently missing).
        sec_missing: Dict[str, List[str]] = {}
        for header_name, label, fix in SECURITY_HEADERS:
            if header_name in ("strict-transport-security", "x-content-type-options"):
                continue  # handled per-page
            missing = [p.url for p in pages if not p.security_headers.get(header_name)]
            if missing and len(missing) == len(pages):
                sec_missing[label] = missing
        for label, urls in sec_missing.items():
            fix_text = next((f for n_, l, f in SECURITY_HEADERS if l == label), "")
            _add("technical", "info", f"{label} header missing",
                 f"The {label} response header is missing on every audited page.",
                 len(urls),
                 affected_urls=urls,
                 how_to_fix=fix_text,
                 impact="low", effort="low", group="technical_debt")

        # Page-level rollups via the rules table.
        rule_map = dict(self.PAGE_ISSUE_RULES)
        for issue, meta in self.PAGE_ISSUE_RULES:
            affected_pages = [p for p in pages if issue in p.issues]
            if not affected_pages:
                continue
            urls = [p.url for p in affected_pages]
            examples = [
                {"url": p.url, "detail": self._format_issue_detail(issue, p)}
                for p in affected_pages
            ]
            _add(meta["category"], meta["severity"], meta["title"], meta["description"],
                 len(urls),
                 affected_urls=urls,
                 examples=examples,
                 how_to_fix=meta.get("how_to_fix"),
                 impact=meta.get("impact"), effort=meta.get("effort"),
                 group=meta.get("group"))

        # Broken links
        if broken_links:
            _add("links", "critical" if len(broken_links) >= 5 else "warning",
                 f"{len(broken_links)} broken outbound link{'s' if len(broken_links) != 1 else ''}",
                 "Broken links waste crawl budget and damage user trust.", len(broken_links),
                 affected_urls=[bl.url for bl in broken_links[:8]],
                 how_to_fix="Fix or remove each broken link; for moved targets use a 301 to the new URL.",
                 impact="medium", effort="low", group="quick_win")

        # Healthy signals
        if all(p.has_schema for p in pages) and pages:
            _add("geo", "success", "All audited pages include structured data",
                 "Schema.org markup is consistently applied — keep it up to date.", n,
                 group="monitoring")
        if all(p.h1_count == 1 for p in pages) and pages:
            _add("on_page", "success", "All audited pages have exactly one <h1>",
                 "Heading structure is clean across the audited pages.", n,
                 group="monitoring")

        # Use rule_map to silence unused-var lint if applicable.
        _ = rule_map
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
            "affected_urls": [],
            "how_to_fix": "Verify DNS, that the server is online, and that no firewall/CDN rule blocks the audit user agent.",
            "impact": "high",
            "effort": "medium",
            "group": "technical_debt",
        }]

    def _compute_recommendations(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return ALL actionable recommendations enriched with how_to_fix /
        impact / effort / group / affected_urls so the dashboard can group
        them (Quick wins / Strategic / Technical debt) instead of showing a
        flat top-10 list.
        """
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
                "affected_urls": f.get("affected_urls") or [],
                "examples": f.get("examples") or [],
                "how_to_fix": f.get("how_to_fix"),
                "impact": f.get("impact"),
                "effort": f.get("effort"),
                "group": f.get("group") or "technical_debt",
            }
            for f in actionable
        ]
