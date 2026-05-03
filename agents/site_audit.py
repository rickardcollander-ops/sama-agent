"""
Site Audit Agent — full SEO + GEO technical audit of a domain.

Crawls the tenant's domain (BFS over internal links, capped at MAX_PAGES),
parses each page's HTML, and scores four categories on a 0–100 scale:
  - technical:  HTTPS, viewport, canonical, sitemap, robots.txt, status codes
  - geo:        schema.org JSON-LD (FAQ, Article, Org, …), structured headings,
                alt-text coverage, language attribute, descriptive titles
  - content:    word count, heading hierarchy, image-alt coverage, title length
  - links:      broken-link rate, internal/external balance, anchor variety

The output shape matches the TypeScript SiteAudit type in
app/c/analysis/types.ts so the dashboard can render it unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


MAX_PAGES = 25
MAX_LINK_CHECKS = 80
CONCURRENCY = 6
PAGE_TIMEOUT = 15.0
LINK_TIMEOUT = 8.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; SamaSiteAuditBot/1.0; "
    "+https://successifier.com/bot)"
)


# ─────────────────────────────────────────────────────────────────────────────
# Data containers
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PageReport:
    url: str
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    title: Optional[str] = None
    title_length: int = 0
    meta_description: Optional[str] = None
    meta_description_length: int = 0
    h1_count: int = 0
    h1_text: Optional[str] = None
    h2_count: int = 0
    h3_count: int = 0
    word_count: int = 0
    canonical: Optional[str] = None
    has_viewport: bool = False
    has_lang: bool = False
    has_og_tags: bool = False
    has_twitter_card: bool = False
    schema_types: List[str] = field(default_factory=list)
    image_count: int = 0
    images_missing_alt: int = 0
    internal_links: int = 0
    external_links: int = 0
    issues: List[str] = field(default_factory=list)
    page_score: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "response_time_ms": self.response_time_ms,
            "title": self.title,
            "title_length": self.title_length,
            "meta_description": self.meta_description,
            "meta_description_length": self.meta_description_length,
            "h1_count": self.h1_count,
            "h1_text": self.h1_text,
            "h2_count": self.h2_count,
            "h3_count": self.h3_count,
            "word_count": self.word_count,
            "canonical": self.canonical,
            "has_viewport": self.has_viewport,
            "has_lang": self.has_lang,
            "has_og_tags": self.has_og_tags,
            "has_twitter_card": self.has_twitter_card,
            "schema_types": self.schema_types,
            "image_count": self.image_count,
            "images_missing_alt": self.images_missing_alt,
            "internal_links": self.internal_links,
            "external_links": self.external_links,
            "issues": self.issues,
            "page_score": self.page_score,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────


class SiteAuditAgent:
    """Tenant-scoped site auditor.

    Usage:
        agent = SiteAuditAgent(domain="example.com")
        report = await agent.run()
    """

    def __init__(self, domain: str, max_pages: int = MAX_PAGES):
        self.domain = self._normalise_domain(domain)
        self.max_pages = max(1, min(max_pages, MAX_PAGES))
        self._link_checks: Dict[str, int] = {}  # url -> status

    # ── public API ──────────────────────────────────────────────────────────

    async def run(self) -> Dict[str, Any]:
        if not self.domain:
            return self._empty_report("No domain configured")

        base_url = f"https://{self.domain}"
        async with httpx.AsyncClient(
            timeout=PAGE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            verify=True,
        ) as http:
            robots = await self._fetch_robots(http, base_url)
            sitemap = await self._fetch_sitemap(http, base_url, robots["sitemap_url"])
            pages = await self._crawl(http, base_url, sitemap["urls"])
            broken = await self._check_external_links(http, pages)

        scores = self._compute_scores(pages, robots, sitemap, broken)
        issues = self._top_issues(pages, robots, sitemap, broken)

        return {
            "domain": self.domain,
            "pages_crawled": len(pages),
            "robots_txt": robots,
            "sitemap": sitemap,
            "scores": scores,
            "issues": issues,
            "broken_links": broken,
            "pages": [p.to_dict() for p in pages],
        }

    # ── crawl ───────────────────────────────────────────────────────────────

    async def _crawl(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        sitemap_urls: List[str],
    ) -> List[PageReport]:
        seed_urls = [base_url] + [u for u in sitemap_urls if self._same_host(u, base_url)]
        seen: Set[str] = set()
        queue: List[str] = []
        for u in seed_urls:
            n = self._canonicalise_url(u)
            if n and n not in seen:
                seen.add(n)
                queue.append(n)

        results: List[PageReport] = []
        sem = asyncio.Semaphore(CONCURRENCY)

        async def fetch(url: str) -> Tuple[PageReport, List[str]]:
            async with sem:
                return await self._fetch_and_parse(http, url, base_url)

        # BFS: process the current frontier in parallel, collect new internal
        # links, repeat until we hit the page cap.
        idx = 0
        while idx < len(queue) and len(results) < self.max_pages:
            batch = queue[idx : idx + CONCURRENCY]
            idx += len(batch)
            outcomes = await asyncio.gather(
                *(fetch(u) for u in batch),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    continue
                page, discovered = outcome
                results.append(page)
                if len(results) >= self.max_pages:
                    break
                for link in discovered:
                    n = self._canonicalise_url(link)
                    if n and n not in seen and self._same_host(n, base_url):
                        seen.add(n)
                        queue.append(n)
        return results

    async def _fetch_and_parse(
        self,
        http: httpx.AsyncClient,
        url: str,
        base_url: str,
    ) -> Tuple[PageReport, List[str]]:
        page = PageReport(url=url)
        try:
            t0 = asyncio.get_event_loop().time()
            resp = await http.get(url)
            page.response_time_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
            page.status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")
            if resp.status_code >= 400 or "html" not in content_type.lower():
                page.issues.append(f"HTTP {resp.status_code}")
                page.page_score = self._score_page(page)
                return page, []
            html = resp.text
        except Exception as e:
            logger.info(f"site-audit fetch failed {url}: {e}")
            page.issues.append("Could not fetch page")
            page.page_score = self._score_page(page)
            return page, []

        soup = BeautifulSoup(html, "lxml")
        self._extract_basics(page, soup)
        discovered = self._extract_links(page, soup, url, base_url)
        page.page_score = self._score_page(page)
        return page, discovered

    def _extract_basics(self, page: PageReport, soup: BeautifulSoup) -> None:
        # Title
        if soup.title and soup.title.string:
            page.title = soup.title.string.strip()
            page.title_length = len(page.title)

        # Meta description
        md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        if md and md.get("content"):
            page.meta_description = md["content"].strip()
            page.meta_description_length = len(page.meta_description)

        # Headings
        h1s = soup.find_all("h1")
        page.h1_count = len(h1s)
        if h1s:
            page.h1_text = (h1s[0].get_text() or "").strip()[:200]
        page.h2_count = len(soup.find_all("h2"))
        page.h3_count = len(soup.find_all("h3"))

        # Word count (text excluding script/style/nav)
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        page.word_count = len(text.split())

        # Canonical
        canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
        if canonical and canonical.get("href"):
            page.canonical = canonical["href"].strip()

        # Viewport
        page.has_viewport = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))

        # Lang
        html_tag = soup.find("html")
        page.has_lang = bool(html_tag and html_tag.get("lang"))

        # OG / Twitter
        page.has_og_tags = bool(soup.find("meta", attrs={"property": re.compile(r"^og:")}))
        page.has_twitter_card = bool(soup.find("meta", attrs={"name": re.compile(r"^twitter:")}))

        # JSON-LD
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
            except Exception:
                continue
            for t in self._extract_schema_types(data):
                if t and t not in page.schema_types:
                    page.schema_types.append(t)

        # Images
        imgs = soup.find_all("img")
        page.image_count = len(imgs)
        page.images_missing_alt = sum(
            1 for img in imgs if not (img.get("alt") or "").strip()
        )

        # Per-page issue summary
        if not page.title:
            page.issues.append("Missing <title>")
        elif page.title_length < 20 or page.title_length > 65:
            page.issues.append(f"Title length {page.title_length} (recommended 20–65)")
        if not page.meta_description:
            page.issues.append("Missing meta description")
        elif page.meta_description_length < 50 or page.meta_description_length > 165:
            page.issues.append(f"Meta description length {page.meta_description_length} (recommended 50–165)")
        if page.h1_count == 0:
            page.issues.append("No <h1> heading")
        elif page.h1_count > 1:
            page.issues.append(f"{page.h1_count} <h1> tags (use one)")
        if page.word_count < 250:
            page.issues.append(f"Thin content ({page.word_count} words)")
        if not page.has_viewport:
            page.issues.append("No viewport meta (mobile)")
        if not page.canonical:
            page.issues.append("No canonical link")
        if page.image_count and page.images_missing_alt / page.image_count > 0.2:
            page.issues.append(
                f"{page.images_missing_alt}/{page.image_count} images missing alt text"
            )
        if not page.schema_types:
            page.issues.append("No schema.org JSON-LD (hurts AI/GEO discovery)")

    def _extract_links(
        self,
        page: PageReport,
        soup: BeautifulSoup,
        url: str,
        base_url: str,
    ) -> List[str]:
        discovered: List[str] = []
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(url, href)
            if self._same_host(absolute, base_url):
                page.internal_links += 1
                discovered.append(absolute)
            else:
                page.external_links += 1
                # Track external links for broken-link checks (capped).
                if absolute not in self._link_checks and len(self._link_checks) < MAX_LINK_CHECKS:
                    self._link_checks[absolute] = 0
        return discovered

    # ── robots & sitemap ────────────────────────────────────────────────────

    async def _fetch_robots(
        self, http: httpx.AsyncClient, base_url: str
    ) -> Dict[str, Any]:
        try:
            resp = await http.get(f"{base_url}/robots.txt")
            if resp.status_code != 200:
                return {"present": False, "sitemap_url": None, "size": 0}
            text = resp.text or ""
            sitemap_url: Optional[str] = None
            for line in text.splitlines():
                m = re.match(r"^\s*sitemap:\s*(\S+)", line, re.I)
                if m:
                    sitemap_url = m.group(1).strip()
                    break
            return {
                "present": True,
                "sitemap_url": sitemap_url,
                "size": len(text),
            }
        except Exception:
            return {"present": False, "sitemap_url": None, "size": 0}

    async def _fetch_sitemap(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        robots_sitemap: Optional[str],
    ) -> Dict[str, Any]:
        candidates = []
        if robots_sitemap:
            candidates.append(robots_sitemap)
        candidates.append(f"{base_url}/sitemap.xml")
        for sm_url in candidates:
            try:
                resp = await http.get(sm_url)
                if resp.status_code != 200:
                    continue
                urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text or "")
                return {
                    "present": True,
                    "url": sm_url,
                    "url_count": len(urls),
                    "urls": urls[: self.max_pages],
                }
            except Exception:
                continue
        return {"present": False, "url": None, "url_count": 0, "urls": []}

    # ── external link health ────────────────────────────────────────────────

    async def _check_external_links(
        self,
        http: httpx.AsyncClient,
        pages: List[PageReport],
    ) -> Dict[str, Any]:
        urls = list(self._link_checks.keys())
        if not urls:
            return {"checked": 0, "broken_count": 0, "broken": []}

        sem = asyncio.Semaphore(CONCURRENCY)

        async def head(u: str) -> Tuple[str, int]:
            async with sem:
                try:
                    resp = await http.head(u, timeout=LINK_TIMEOUT)
                    code = resp.status_code
                    # Some servers reject HEAD; retry with GET when 405/501.
                    if code in (405, 501):
                        resp = await http.get(u, timeout=LINK_TIMEOUT)
                        code = resp.status_code
                    return u, code
                except Exception:
                    return u, 0

        outcomes = await asyncio.gather(*(head(u) for u in urls), return_exceptions=False)
        broken: List[Dict[str, Any]] = []
        for url, code in outcomes:
            self._link_checks[url] = code
            if code == 0 or code >= 400:
                broken.append({"url": url, "status": code})
        return {
            "checked": len(urls),
            "broken_count": len(broken),
            "broken": broken[:20],
        }

    # ── scoring ─────────────────────────────────────────────────────────────

    def _score_page(self, p: PageReport) -> int:
        score = 100
        if not p.title:
            score -= 15
        elif p.title_length < 20 or p.title_length > 65:
            score -= 5
        if not p.meta_description:
            score -= 10
        elif p.meta_description_length < 50 or p.meta_description_length > 165:
            score -= 4
        if p.h1_count == 0:
            score -= 10
        elif p.h1_count > 1:
            score -= 4
        if p.word_count < 250:
            score -= 8
        if not p.has_viewport:
            score -= 5
        if not p.canonical:
            score -= 5
        if not p.schema_types:
            score -= 8
        if p.image_count:
            missing_ratio = p.images_missing_alt / p.image_count
            if missing_ratio > 0.5:
                score -= 8
            elif missing_ratio > 0.2:
                score -= 4
        if p.status_code and p.status_code >= 400:
            score = max(0, min(score, 30))
        return max(0, min(100, score))

    def _compute_scores(
        self,
        pages: List[PageReport],
        robots: Dict[str, Any],
        sitemap: Dict[str, Any],
        broken: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not pages:
            return {
                "overall": 0,
                "technical": 0,
                "geo": 0,
                "content": 0,
                "links": 0,
                "details": {},
            }

        n = len(pages)
        ok = sum(1 for p in pages if p.status_code and 200 <= p.status_code < 400)
        with_canonical = sum(1 for p in pages if p.canonical)
        with_viewport = sum(1 for p in pages if p.has_viewport)
        with_lang = sum(1 for p in pages if p.has_lang)
        with_og = sum(1 for p in pages if p.has_og_tags)
        with_schema = sum(1 for p in pages if p.schema_types)
        faq_or_howto = sum(
            1 for p in pages
            if any(t in {"FAQPage", "HowTo", "Article", "BlogPosting", "Product"} for t in p.schema_types)
        )
        with_h1 = sum(1 for p in pages if p.h1_count == 1)
        with_title = sum(1 for p in pages if p.title and 20 <= p.title_length <= 65)
        with_meta = sum(1 for p in pages if p.meta_description and 50 <= p.meta_description_length <= 165)
        long_enough = sum(1 for p in pages if p.word_count >= 250)
        avg_words = sum(p.word_count for p in pages) / n
        total_imgs = sum(p.image_count for p in pages)
        missing_alt = sum(p.images_missing_alt for p in pages)
        alt_coverage = 1.0 if total_imgs == 0 else 1.0 - (missing_alt / total_imgs)

        # Technical: HTTPS implicit (we requested https://), uptime, sitemap,
        # robots, canonical, viewport.
        technical = round(
            100
            * (
                0.20 * (ok / n)
                + 0.15 * (1.0 if robots["present"] else 0.0)
                + 0.15 * (1.0 if sitemap["present"] else 0.0)
                + 0.20 * (with_canonical / n)
                + 0.20 * (with_viewport / n)
                + 0.10 * (with_lang / n)
            )
        )

        # GEO/AI readiness: schema markup, FAQ/Article schemas, alt text,
        # language attribute, OG metadata, descriptive titles.
        geo = round(
            100
            * (
                0.30 * (with_schema / n)
                + 0.20 * (faq_or_howto / n)
                + 0.15 * alt_coverage
                + 0.10 * (with_lang / n)
                + 0.10 * (with_og / n)
                + 0.15 * (with_title / n)
            )
        )

        # Content: word count, headings, meta descriptions, alt text.
        content = round(
            100
            * (
                0.30 * (long_enough / n)
                + 0.25 * (with_h1 / n)
                + 0.20 * (with_meta / n)
                + 0.15 * alt_coverage
                + 0.10 * min(avg_words / 800.0, 1.0)
            )
        )

        # Links: broken-link rate + internal-link density.
        avg_internal = sum(p.internal_links for p in pages) / n
        broken_rate = (broken["broken_count"] / broken["checked"]) if broken["checked"] else 0.0
        links = round(
            100
            * (
                0.55 * (1.0 - broken_rate)
                + 0.25 * min(avg_internal / 10.0, 1.0)
                + 0.20 * (1.0 if sitemap["present"] else 0.0)
            )
        )

        overall = round(0.30 * technical + 0.30 * geo + 0.25 * content + 0.15 * links)

        return {
            "overall": overall,
            "technical": technical,
            "geo": geo,
            "content": content,
            "links": links,
            "details": {
                "pages_ok": ok,
                "pages_total": n,
                "robots_present": robots["present"],
                "sitemap_present": sitemap["present"],
                "sitemap_url_count": sitemap.get("url_count", 0),
                "with_canonical": with_canonical,
                "with_viewport": with_viewport,
                "with_lang": with_lang,
                "with_og": with_og,
                "with_schema": with_schema,
                "with_faq_or_article_schema": faq_or_howto,
                "with_proper_h1": with_h1,
                "with_good_title": with_title,
                "with_good_meta": with_meta,
                "long_enough_pages": long_enough,
                "avg_word_count": round(avg_words),
                "alt_coverage": round(alt_coverage * 100),
                "avg_internal_links": round(avg_internal, 1),
                "broken_link_rate": round(broken_rate * 100, 1),
            },
        }

    def _top_issues(
        self,
        pages: List[PageReport],
        robots: Dict[str, Any],
        sitemap: Dict[str, Any],
        broken: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        n = len(pages) or 1

        if not robots["present"]:
            issues.append({"severity": "high", "category": "technical",
                           "title": "robots.txt missing",
                           "detail": "Search engines and AI crawlers expect a robots.txt at /robots.txt."})
        if not sitemap["present"]:
            issues.append({"severity": "high", "category": "technical",
                           "title": "sitemap.xml missing",
                           "detail": "Add a sitemap so crawlers can discover all your pages."})

        without_schema = sum(1 for p in pages if not p.schema_types)
        if without_schema / n >= 0.5:
            issues.append({"severity": "high", "category": "geo",
                           "title": f"{without_schema}/{n} pages have no schema markup",
                           "detail": "Add JSON-LD (Article, FAQPage, Product, Organization) to help AI engines cite you."})

        thin = [p for p in pages if p.word_count < 250]
        if len(thin) / n >= 0.3:
            issues.append({"severity": "medium", "category": "content",
                           "title": f"{len(thin)} thin pages (<250 words)",
                           "detail": "Expand thin pages with substantive copy that answers buyer questions."})

        no_meta = [p for p in pages if not p.meta_description]
        if len(no_meta) / n >= 0.3:
            issues.append({"severity": "medium", "category": "technical",
                           "title": f"{len(no_meta)} pages missing meta description",
                           "detail": "Meta descriptions drive click-through from SERPs and AI summaries."})

        no_h1 = [p for p in pages if p.h1_count != 1]
        if len(no_h1) / n >= 0.3:
            issues.append({"severity": "medium", "category": "content",
                           "title": f"{len(no_h1)} pages with bad <h1>",
                           "detail": "Each page should have exactly one descriptive <h1>."})

        if broken["broken_count"]:
            issues.append({"severity": "high", "category": "links",
                           "title": f"{broken['broken_count']} broken external links",
                           "detail": "Broken links erode trust and crawl budget. Fix or remove."})

        no_canonical = sum(1 for p in pages if not p.canonical)
        if no_canonical / n >= 0.3:
            issues.append({"severity": "medium", "category": "technical",
                           "title": f"{no_canonical} pages without canonical",
                           "detail": "Add <link rel=\"canonical\"> to avoid duplicate-content penalties."})

        # Surface most common per-page issue too.
        all_issues = [iss for p in pages for iss in p.issues]
        if all_issues:
            common = Counter(all_issues).most_common(3)
            for label, count in common:
                if count / n >= 0.4:
                    issues.append({"severity": "low", "category": "content",
                                   "title": f"{count} pages: {label}",
                                   "detail": "Common across many pages — fix at the template level."})

        return issues[:10]

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_domain(raw: str) -> str:
        if not raw:
            return ""
        d = raw.strip().lower()
        d = re.sub(r"^https?://", "", d)
        d = d.split("/")[0]
        if d.startswith("www."):
            d = d[4:]
        return d

    @staticmethod
    def _same_host(url: str, base_url: str) -> bool:
        try:
            a = urlparse(url).netloc.lower().lstrip("www.")
            b = urlparse(base_url).netloc.lower().lstrip("www.")
            return bool(a) and a == b
        except Exception:
            return False

    @staticmethod
    def _canonicalise_url(url: str) -> Optional[str]:
        try:
            p = urlparse(url)
            if p.scheme not in ("http", "https"):
                return None
            # Drop fragment, trailing slash on root paths only.
            path = p.path or "/"
            return urlunparse((p.scheme, p.netloc.lower(), path, "", p.query, ""))
        except Exception:
            return None

    @staticmethod
    def _extract_schema_types(data: Any) -> List[str]:
        out: List[str] = []
        if isinstance(data, dict):
            t = data.get("@type")
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, list):
                out.extend([x for x in t if isinstance(x, str)])
            for v in data.values():
                out.extend(SiteAuditAgent._extract_schema_types(v))
        elif isinstance(data, list):
            for item in data:
                out.extend(SiteAuditAgent._extract_schema_types(item))
        return out

    @staticmethod
    def _empty_report(reason: str) -> Dict[str, Any]:
        return {
            "domain": "",
            "pages_crawled": 0,
            "robots_txt": {"present": False, "sitemap_url": None, "size": 0},
            "sitemap": {"present": False, "url": None, "url_count": 0, "urls": []},
            "scores": {"overall": 0, "technical": 0, "geo": 0, "content": 0, "links": 0, "details": {}},
            "issues": [{"severity": "high", "category": "technical", "title": reason, "detail": ""}],
            "broken_links": {"checked": 0, "broken_count": 0, "broken": []},
            "pages": [],
        }
