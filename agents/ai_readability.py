"""
ai_readability — measures how well crawled pages are structured for AI
ingestion. Runs as a post-step after SiteAuditAgent.audit_domain finishes,
inside ``api/routes/site_audit.py``.

Why this lives outside the audit agent:
  We need only 3 of the audited pages (homepage + 2 most internally-linked)
  but we need their raw HTML, which the audit payload deliberately strips
  to keep JSONB size sane. Re-fetching 3 URLs is cheaper than dragging the
  full HTML through serialization, and it keeps the audit agent unchanged.

Output schema (embedded in site_audits.payload as ``ai_readability``):

    {
        "overall_score": 0-100,
        "sub_scores": {
            "structure": 0-100,
            "metadata": 0-100,
            "chunking": 0-100,
            "semantics": 0-100,
            "navigation": 0-100,
        },
        "action_points": [
            {
                "title": str,
                "category": "structure"|"metadata"|"chunking"|"semantics"|"navigation",
                "priority": "P1"|"P2",
                "description": str,
                "why": str,
                "code_example": str|None,
                "estimated_time": str,
                "estimated_impact": str,
            },
            ...
        ],
        "page_analyses": [
            {
                "url": str,
                "sub_scores": {...},
                "chunks": [
                    {
                        "id": str,
                        "text": str,
                        "type": "heading_block"|"paragraph"|"list"|"quote"|"table",
                        "dom_path": str,
                        "has_heading": bool,
                        "is_redundant": bool,
                        "semantic_clarity": 0-10|None,
                        "featured_snippet_score": 0-10|None,
                        "featured_snippet_potential": bool|None,
                        "ai_interpretation": str|None,
                        "recommendations": [str],
                        "issues": [str],
                    },
                    ...
                ],
            },
            ...
        ],
    }

Both LLM calls (per-chunk scoring + action-point synthesis) degrade
gracefully: when no Anthropic key is configured, the deterministic
fallback returns the same shape with ``semantic_clarity=None`` on chunks
and a rule-based action-point list keyed off the sub-scores.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────────

# We score the homepage plus the two most internally-linked sub-pages. More
# than three pages quickly multiplies LLM cost without proportional insight
# at the MVP stage; widening this cap is a cheap follow-up if users ask.
PAGES_TO_SCORE = 3
# Hard cap on chunks per page sent to the LLM. Long marketing pages can
# easily produce 80+ chunks; sending all of them blows the input budget and
# dilutes the model's attention. 30 covers the meaningful sections of any
# homepage we've seen during development.
MAX_CHUNKS_PER_PAGE = 30
# Sub-score weights used to compute the overall score. Sums to 100.
SUB_SCORE_WEIGHTS = {
    "structure": 20,
    "metadata": 20,
    "chunking": 25,
    "semantics": 20,
    "navigation": 15,
}
# Browser-ish UA for the re-fetch step. The audit already crawled the same
# pages with its own UA, but some sites differentiate; using a plain UA
# keeps content the same as what an AI assistant would see.
USER_AGENT = (
    "Mozilla/5.0 (compatible; SamaAIReadability/1.0; +https://successifier.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_S = 15.0


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    id: str
    text: str
    type: str  # heading_block | paragraph | list | quote | table
    dom_path: str
    has_heading: bool = False
    is_redundant: bool = False
    semantic_clarity: Optional[float] = None
    featured_snippet_score: Optional[float] = None
    featured_snippet_potential: Optional[bool] = None
    ai_interpretation: Optional[str] = None
    recommendations: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type,
            "dom_path": self.dom_path,
            "has_heading": self.has_heading,
            "is_redundant": self.is_redundant,
            "semantic_clarity": self.semantic_clarity,
            "featured_snippet_score": self.featured_snippet_score,
            "featured_snippet_potential": self.featured_snippet_potential,
            "ai_interpretation": self.ai_interpretation,
            "recommendations": self.recommendations,
            "issues": self.issues,
        }


# ── Public entry points ──────────────────────────────────────────────────────

async def score_audit(
    audit_payload: Dict[str, Any],
    *,
    anthropic_key: Optional[str] = None,
    tenant_id: str = "default",
    model: str = "claude-sonnet-4-6",
) -> Dict[str, Any]:
    """Run AI-readability scoring against an existing audit payload.

    Selects the homepage + 2 most-internally-linked pages from
    ``audit_payload["pages"]``, refetches their HTML, chunks it, and scores
    each chunk. Returns the full report dict (see module docstring).
    """
    pages = audit_payload.get("pages") or []
    if not pages:
        return _empty_report("audit had no pages")

    selected = _select_pages(pages)
    if not selected:
        return _empty_report("no analysable pages")

    html_by_url = await _fetch_pages(selected)
    if not html_by_url:
        return _empty_report("could not refetch any selected page")

    summary = audit_payload.get("summary") or {}
    sitemap_url_count = int(summary.get("total_pages_discovered") or 0)
    has_sitemap_xml = bool(summary.get("has_sitemap_xml"))

    return await score_pages(
        selected,
        html_by_url,
        sitemap_url_count=sitemap_url_count,
        has_sitemap_xml=has_sitemap_xml,
        anthropic_key=anthropic_key,
        tenant_id=tenant_id,
        model=model,
    )


async def score_pages(
    pages: List[Dict[str, Any]],
    html_by_url: Dict[str, str],
    *,
    sitemap_url_count: int = 0,
    has_sitemap_xml: bool = False,
    anthropic_key: Optional[str] = None,
    tenant_id: str = "default",
    model: str = "claude-sonnet-4-6",
) -> Dict[str, Any]:
    """Score the given pages (each represented as a dict with the same
    keys as ``PageReport.to_dict()``) using their associated HTML."""
    if not pages:
        return _empty_report("no pages provided")

    page_analyses: List[Dict[str, Any]] = []
    for page in pages:
        url = page.get("url")
        if not url:
            continue
        html = html_by_url.get(url)
        if not html:
            continue
        chunks = _chunk_html(html)
        if not chunks:
            continue
        chunks = chunks[:MAX_CHUNKS_PER_PAGE]
        if anthropic_key:
            try:
                chunks = await _llm_score_chunks(
                    chunks=chunks,
                    page=page,
                    anthropic_key=anthropic_key,
                    tenant_id=tenant_id,
                    model=model,
                )
            except Exception as e:
                logger.info("ai_readability LLM chunk scoring failed: %s", e)
        page_sub_scores = _page_sub_scores(
            page,
            chunks,
            sitemap_url_count=sitemap_url_count,
            has_sitemap_xml=has_sitemap_xml,
        )
        page_analyses.append({
            "url": url,
            "sub_scores": page_sub_scores,
            "chunks": [c.to_dict() for c in chunks],
        })

    if not page_analyses:
        return _empty_report("no chunks extracted")

    sub_scores = _aggregate_sub_scores([pa["sub_scores"] for pa in page_analyses])
    overall = _weighted_overall(sub_scores)

    issues_summary = _collect_top_issues(pages, page_analyses)
    if anthropic_key:
        try:
            action_points = await _llm_action_points(
                sub_scores=sub_scores,
                issues=issues_summary,
                anthropic_key=anthropic_key,
                tenant_id=tenant_id,
                model=model,
            )
        except Exception as e:
            logger.info("ai_readability LLM action points failed: %s", e)
            action_points = _fallback_action_points(sub_scores, issues_summary)
    else:
        action_points = _fallback_action_points(sub_scores, issues_summary)

    return {
        "overall_score": overall,
        "sub_scores": sub_scores,
        "action_points": action_points,
        "page_analyses": page_analyses,
    }


def _empty_report(reason: str) -> Dict[str, Any]:
    return {
        "overall_score": None,
        "sub_scores": {k: None for k in SUB_SCORE_WEIGHTS},
        "action_points": [],
        "page_analyses": [],
        "skipped_reason": reason,
    }


# ── Page selection + fetch ───────────────────────────────────────────────────

def _select_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Homepage + the two most-internally-linked sub-pages."""
    only_loaded = [p for p in pages if p.get("status_code") == 200 and p.get("url")]
    if not only_loaded:
        return []
    # Homepage heuristic: shortest URL path (i.e. "/", "/sv/", "/en/").
    sorted_by_len = sorted(only_loaded, key=lambda p: len(p["url"]))
    homepage = sorted_by_len[0]
    rest = [p for p in only_loaded if p["url"] != homepage["url"]]
    rest.sort(key=lambda p: int(p.get("internal_links") or 0), reverse=True)
    return [homepage] + rest[: PAGES_TO_SCORE - 1]


async def _fetch_pages(pages: List[Dict[str, Any]]) -> Dict[str, str]:
    """Refetch HTML for a small set of URLs in parallel. Drops pages that
    fail to load — caller decides what to do with the partial result."""
    out: Dict[str, str] = {}

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
        max_redirects=10,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
    ) as client:
        async def _one(url: str) -> Tuple[str, Optional[str]]:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return url, None
                ct = (resp.headers.get("content-type") or "").lower()
                if "html" not in ct and ct:
                    return url, None
                return url, resp.text
            except Exception as e:
                logger.info("ai_readability fetch failed for %s: %s", url, e)
                return url, None

        tasks = [_one(p["url"]) for p in pages]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        for url, html in results:
            if html:
                out[url] = html
    return out


# ── HTML chunking ────────────────────────────────────────────────────────────

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_CONTENT_TAGS = {"p", "ul", "ol", "blockquote", "table", "figure", "pre"}


def _chunk_html(html: str) -> List[Chunk]:
    """Split rendered page text into semantic chunks the LLM can score
    individually. Heuristic, no LLM call. Mirrors how an AI assistant would
    actually segment the page when ingesting it for retrieval."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "form"]):
        tag.decompose()

    chunks: List[Chunk] = []
    seen_signatures: List[str] = []

    def _signature(text: str) -> str:
        # Tokens used for jaccard-style redundancy detection. Stripping
        # punctuation prevents trivial differences (like trailing ".") from
        # making two paragraphs look distinct.
        return " ".join(re.findall(r"\b\w+\b", text.lower())[:30])

    def _is_redundant(sig: str) -> bool:
        if not sig:
            return False
        sig_tokens = set(sig.split())
        if len(sig_tokens) < 5:
            return False
        for prev in seen_signatures:
            prev_tokens = set(prev.split())
            if not prev_tokens:
                continue
            jaccard = len(sig_tokens & prev_tokens) / max(1, len(sig_tokens | prev_tokens))
            if jaccard >= 0.85:
                return True
        return False

    def _dom_path(tag: Tag) -> str:
        parts: List[str] = []
        cur: Optional[Tag] = tag
        depth = 0
        while cur is not None and depth < 4 and cur.name:
            parts.append(cur.name)
            cur = cur.parent if isinstance(cur.parent, Tag) else None
            depth += 1
        return ">".join(reversed(parts))

    body = soup.body or soup
    counter = 0

    for el in body.find_all(list(_HEADING_TAGS | _CONTENT_TAGS), recursive=True):
        if not isinstance(el, Tag):
            continue
        text = el.get_text(" ", strip=True)
        if not text or len(text) < 20:
            continue
        # Cap individual chunk size — long article bodies become a single
        # paragraph in some templates, which is bad for retrieval anyway.
        if len(text) > 1200:
            text = text[:1200].rsplit(" ", 1)[0] + "…"

        if el.name in _HEADING_TAGS:
            chunk_type = "heading_block"
            has_heading = True
        elif el.name in {"ul", "ol"}:
            chunk_type = "list"
            has_heading = False
        elif el.name == "blockquote":
            chunk_type = "quote"
            has_heading = False
        elif el.name == "table":
            chunk_type = "table"
            has_heading = False
        else:
            chunk_type = "paragraph"
            has_heading = False

        sig = _signature(text)
        redundant = _is_redundant(sig)
        if not redundant:
            seen_signatures.append(sig)

        counter += 1
        chunks.append(Chunk(
            id=f"c{counter}",
            text=text,
            type=chunk_type,
            dom_path=_dom_path(el),
            has_heading=has_heading,
            is_redundant=redundant,
        ))
    return chunks


# ── LLM: per-chunk scoring ───────────────────────────────────────────────────

async def _llm_score_chunks(
    *,
    chunks: List[Chunk],
    page: Dict[str, Any],
    anthropic_key: str,
    tenant_id: str,
    model: str,
) -> List[Chunk]:
    try:
        from anthropic import Anthropic
    except Exception:
        return chunks

    from shared.llm import call_claude

    chunk_payload = [
        {"id": c.id, "type": c.type, "text": c.text}
        for c in chunks
    ]
    page_meta = {
        "url": page.get("url"),
        "title": page.get("title") or "",
        "meta_description": page.get("meta_description") or "",
    }

    system = (
        "You score how well individual content chunks of a marketing/website "
        "page are structured for an AI assistant to ingest, retrieve, and "
        "cite. Be terse, concrete, and consistent across chunks. Always "
        "output strict JSON — no prose, no markdown fences."
    )
    prompt = (
        "Page context:\n"
        f"{json.dumps(page_meta, ensure_ascii=False)}\n\n"
        "Chunks (in document order):\n"
        f"{json.dumps(chunk_payload, ensure_ascii=False)}\n\n"
        "For EACH chunk return a JSON object with these fields:\n"
        "  id (string, copy from input)\n"
        "  semantic_clarity (number 0-10: how self-contained and unambiguous "
        "the chunk is when read in isolation)\n"
        "  featured_snippet_score (number 0-10: how likely the chunk is to be "
        "selected as a direct answer in a search/AI snippet)\n"
        "  featured_snippet_potential (boolean: featured_snippet_score >= 6)\n"
        "  ai_interpretation (string, ONE concise sentence describing what "
        "an AI would conclude this chunk is about)\n"
        "  recommendations (array of up to 2 short improvement suggestions)\n"
        "  issues (array of up to 2 short problems if present, else [])\n\n"
        "Output a JSON array of those objects, ordered to match the input. "
        "No other keys, no surrounding text."
    )

    client = Anthropic(api_key=anthropic_key)
    msg = await call_claude(
        client=client,
        model=model,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        tenant_id=tenant_id,
        max_tokens=3000,
    )
    text = (msg.content[0].text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        logger.info("ai_readability chunk scoring returned non-JSON")
        return chunks
    if not isinstance(parsed, list):
        return chunks

    by_id = {item.get("id"): item for item in parsed if isinstance(item, dict)}
    for c in chunks:
        item = by_id.get(c.id)
        if not item:
            continue
        c.semantic_clarity = _clamp_num(item.get("semantic_clarity"), 0, 10)
        c.featured_snippet_score = _clamp_num(item.get("featured_snippet_score"), 0, 10)
        c.featured_snippet_potential = bool(item.get("featured_snippet_potential"))
        ai_interp = item.get("ai_interpretation")
        if isinstance(ai_interp, str):
            c.ai_interpretation = ai_interp.strip()[:240]
        recs = item.get("recommendations") or []
        c.recommendations = [str(r).strip() for r in recs if r][:2]
        iss = item.get("issues") or []
        c.issues = [str(i).strip() for i in iss if i][:2]
    return chunks


def _clamp_num(value: Any, lo: float, hi: float) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


# ── Sub-score computation ────────────────────────────────────────────────────

def _page_sub_scores(
    page: Dict[str, Any],
    chunks: List[Chunk],
    *,
    sitemap_url_count: int,
    has_sitemap_xml: bool,
) -> Dict[str, int]:
    """Per-page sub-scores derived from the crawled signal buffet plus the
    LLM-scored chunks. Each component is 0–100."""
    total_chunks = len(chunks) or 1

    title = page.get("title") or ""
    title_len = int(page.get("title_length") or 0)
    meta_desc = page.get("meta_description") or ""
    meta_desc_len = int(page.get("meta_description_length") or 0)
    h1_count = int(page.get("h1_count") or 0)
    h2_count = int(page.get("h2_count") or 0)
    canonical = page.get("canonical")
    has_open_graph = bool(page.get("has_open_graph"))
    has_schema = bool(page.get("has_schema"))
    has_lang = bool(page.get("has_lang"))
    images_total = int(page.get("images_total") or 0)
    images_missing_alt = int(page.get("images_missing_alt") or 0)
    internal_links = int(page.get("internal_links") or 0)

    # Structure: heading hierarchy + presence of semantic block elements.
    h_score = 100 if h1_count == 1 else (60 if h1_count > 0 else 0)
    if h2_count == 0:
        h_score = min(h_score, 50)
    has_heading_block = sum(1 for c in chunks if c.has_heading)
    structure = (h_score * 0.6) + (min(100, has_heading_block * 100 / total_chunks) * 0.4)

    # Metadata: title length window, description, canonical, OG, schema.
    metadata = 0.0
    metadata += 20 if title and 30 <= title_len <= 65 else (10 if title else 0)
    metadata += 20 if meta_desc and 80 <= meta_desc_len <= 165 else (10 if meta_desc else 0)
    metadata += 20 if canonical else 0
    metadata += 20 if has_open_graph else 0
    metadata += 20 if has_schema else 0

    # Chunking: mean clarity (0–10 → 0–100), share of chunks with heading,
    # penalty for redundancy.
    clarities = [c.semantic_clarity for c in chunks if c.semantic_clarity is not None]
    if clarities:
        clarity = sum(clarities) / len(clarities) * 10
    else:
        # No LLM scores: derive a rough heuristic from chunk length.
        avg_len = sum(len(c.text) for c in chunks) / total_chunks
        clarity = 60 if 80 <= avg_len <= 600 else 40
    heading_share = (has_heading_block / total_chunks) * 100
    redundancy_share = sum(1 for c in chunks if c.is_redundant) / total_chunks
    chunking = (clarity * 0.6) + (heading_share * 0.3) - (redundancy_share * 100 * 0.2)
    chunking = max(0.0, chunking)

    # Semantics: alt-text coverage, AI interpretation density, schema types.
    if images_total > 0:
        alt_cov = (images_total - images_missing_alt) / images_total * 100
    else:
        alt_cov = 100
    interp_share = sum(1 for c in chunks if c.ai_interpretation) / total_chunks * 100
    semantic_score = (alt_cov * 0.5) + (interp_share * 0.3) + (20 if has_schema else 0)

    # Navigation: sitemap coverage vs internal links + lang/hreflang.
    if has_sitemap_xml and internal_links:
        coverage = min(100, sitemap_url_count * 100 / max(1, internal_links))
    else:
        coverage = 50 if has_sitemap_xml else 20
    nav = (coverage * 0.6) + (40 if has_lang else 0)

    return {
        "structure": int(round(_clamp(structure, 0, 100))),
        "metadata": int(round(_clamp(metadata, 0, 100))),
        "chunking": int(round(_clamp(chunking, 0, 100))),
        "semantics": int(round(_clamp(semantic_score, 0, 100))),
        "navigation": int(round(_clamp(nav, 0, 100))),
    }


def _clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def _aggregate_sub_scores(per_page: List[Dict[str, int]]) -> Dict[str, int]:
    if not per_page:
        return {k: 0 for k in SUB_SCORE_WEIGHTS}
    out: Dict[str, int] = {}
    for key in SUB_SCORE_WEIGHTS:
        values = [pp.get(key, 0) for pp in per_page]
        out[key] = int(round(sum(values) / len(values)))
    return out


def _weighted_overall(sub_scores: Dict[str, int]) -> int:
    total_weight = sum(SUB_SCORE_WEIGHTS.values()) or 1
    weighted = sum(
        sub_scores.get(k, 0) * w for k, w in SUB_SCORE_WEIGHTS.items()
    )
    return int(round(weighted / total_weight))


# ── Action points ────────────────────────────────────────────────────────────

def _collect_top_issues(
    pages: List[Dict[str, Any]], page_analyses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compact summary of the worst signals across the audited pages — used
    as input for the LLM that synthesises action points."""
    total_imgs = sum(int(p.get("images_total") or 0) for p in pages)
    missing_alt = sum(int(p.get("images_missing_alt") or 0) for p in pages)
    pages_missing_meta_desc = sum(1 for p in pages if not p.get("meta_description"))
    pages_missing_canonical = sum(1 for p in pages if not p.get("canonical"))
    pages_missing_schema = sum(1 for p in pages if not p.get("has_schema"))
    pages_missing_og = sum(1 for p in pages if not p.get("has_open_graph"))
    pages_missing_h1 = sum(1 for p in pages if int(p.get("h1_count") or 0) == 0)

    redundancy_chunks = 0
    low_clarity_chunks = 0
    total_chunks = 0
    for pa in page_analyses:
        for c in pa.get("chunks", []):
            total_chunks += 1
            if c.get("is_redundant"):
                redundancy_chunks += 1
            sc = c.get("semantic_clarity")
            if sc is not None and sc < 5:
                low_clarity_chunks += 1

    return {
        "pages_audited": len(pages),
        "total_images": total_imgs,
        "images_missing_alt": missing_alt,
        "pages_missing_meta_desc": pages_missing_meta_desc,
        "pages_missing_canonical": pages_missing_canonical,
        "pages_missing_schema": pages_missing_schema,
        "pages_missing_open_graph": pages_missing_og,
        "pages_missing_h1": pages_missing_h1,
        "total_chunks_scored": total_chunks,
        "redundant_chunks": redundancy_chunks,
        "low_clarity_chunks": low_clarity_chunks,
    }


async def _llm_action_points(
    *,
    sub_scores: Dict[str, int],
    issues: Dict[str, Any],
    anthropic_key: str,
    tenant_id: str,
    model: str,
) -> List[Dict[str, Any]]:
    try:
        from anthropic import Anthropic
    except Exception:
        return _fallback_action_points(sub_scores, issues)

    from shared.llm import call_claude

    system = (
        "You generate concrete, prioritised action points for improving how "
        "well a website's HTML is structured for AI assistants to ingest. "
        "Output strict JSON — no prose, no markdown fences."
    )
    prompt = (
        "Sub-scores (0-100, lower is worse):\n"
        f"{json.dumps(sub_scores, ensure_ascii=False)}\n\n"
        "Aggregated issues across audited pages:\n"
        f"{json.dumps(issues, ensure_ascii=False)}\n\n"
        "Produce 4–8 action points addressing the lowest sub-scores and the "
        "most-frequent issues. For each action point output:\n"
        "  title (string, ≤ 60 chars, imperative)\n"
        "  category (one of: structure, metadata, chunking, semantics, navigation)\n"
        "  priority ('P1' for high-impact issues, 'P2' for medium)\n"
        "  description (string, ≤ 240 chars, what to do)\n"
        "  why (string, ≤ 200 chars, why it matters for AI ingestion)\n"
        "  code_example (string with a tiny HTML snippet illustrating the fix, "
        "or null when not applicable)\n"
        "  estimated_time (string, e.g. '30 minutes', '2 hours')\n"
        "  estimated_impact (string, ≤ 100 chars, e.g. '+15% AI comprehension')\n\n"
        "Output a JSON array. Order by descending priority then by category."
    )

    client = Anthropic(api_key=anthropic_key)
    msg = await call_claude(
        client=client,
        model=model,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        tenant_id=tenant_id,
        max_tokens=2200,
    )
    text = (msg.content[0].text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return _fallback_action_points(sub_scores, issues)
    if not isinstance(parsed, list):
        return _fallback_action_points(sub_scores, issues)

    out: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        category = str(item.get("category") or "structure").strip().lower()
        if category not in SUB_SCORE_WEIGHTS:
            category = "structure"
        priority = str(item.get("priority") or "P2").strip().upper()
        if priority not in ("P1", "P2"):
            priority = "P2"
        out.append({
            "title": title[:80],
            "category": category,
            "priority": priority,
            "description": str(item.get("description") or "").strip()[:300],
            "why": str(item.get("why") or "").strip()[:240],
            "code_example": (item.get("code_example") or None),
            "estimated_time": str(item.get("estimated_time") or "").strip()[:40],
            "estimated_impact": str(item.get("estimated_impact") or "").strip()[:120],
        })
    return out[:8] or _fallback_action_points(sub_scores, issues)


def _fallback_action_points(
    sub_scores: Dict[str, int], issues: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Deterministic action points used when no LLM is available. Maps each
    weak sub-score and frequent issue to a pre-written recommendation."""
    out: List[Dict[str, Any]] = []
    if issues.get("pages_missing_h1", 0) > 0:
        out.append({
            "title": "Add a single, clear H1 to every page",
            "category": "structure",
            "priority": "P1",
            "description": (
                "Pages missing a top-level heading make it hard for an AI to "
                "identify the page's main topic. Use exactly one H1 per page."
            ),
            "why": "H1 is the strongest semantic signal for what a page is about.",
            "code_example": "<h1>Reduce SaaS churn with proactive onboarding</h1>",
            "estimated_time": "30 minutes",
            "estimated_impact": "+15% topic clarity for AI ingestion",
        })
    if issues.get("images_missing_alt", 0) > 0:
        total = max(1, issues.get("total_images", 1))
        share = int(issues["images_missing_alt"] * 100 / total)
        out.append({
            "title": "Add descriptive alt text to images missing it",
            "category": "semantics",
            "priority": "P1" if share > 30 else "P2",
            "description": (
                f"{issues['images_missing_alt']} of {issues.get('total_images', 0)} "
                "images have no alt attribute. AI assistants rely on alt text "
                "to interpret visual content."
            ),
            "why": "Alt text is the only way an AI can describe an image to a user.",
            "code_example": '<img src="dashboard.png" alt="SAMA dashboard showing weekly traffic chart">',
            "estimated_time": "1 hour",
            "estimated_impact": "+10% multimodal AI comprehension",
        })
    if issues.get("pages_missing_meta_desc", 0) > 0:
        out.append({
            "title": "Write a meta description for every page",
            "category": "metadata",
            "priority": "P2",
            "description": (
                "Missing meta descriptions force AI assistants to summarise "
                "the page from body text — often poorly. Aim for 80–160 chars."
            ),
            "why": "Meta description is the canonical short summary for retrieval.",
            "code_example": '<meta name="description" content="Short, factual summary of the page topic.">',
            "estimated_time": "1 hour",
            "estimated_impact": "+8% retrieval precision",
        })
    if issues.get("pages_missing_schema", 0) > 0:
        out.append({
            "title": "Add Schema.org JSON-LD structured data",
            "category": "metadata",
            "priority": "P2",
            "description": (
                "Pages without Schema.org JSON-LD lose semantic context that "
                "AI search engines rely on for entity extraction."
            ),
            "why": "Structured data is the highest-confidence signal for entity types.",
            "code_example": (
                '<script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Organization",'
                '"name":"Example","url":"https://example.com"}</script>'
            ),
            "estimated_time": "2 hours",
            "estimated_impact": "+12% entity recognition by AI search",
        })
    if issues.get("redundant_chunks", 0) >= 3:
        out.append({
            "title": "Remove duplicate or near-duplicate content blocks",
            "category": "chunking",
            "priority": "P2",
            "description": (
                f"{issues['redundant_chunks']} chunks repeat earlier content. "
                "Deduplicate so each chunk carries unique information."
            ),
            "why": "Repetitive content dilutes retrieval and lowers per-chunk relevance.",
            "code_example": None,
            "estimated_time": "1 hour",
            "estimated_impact": "+10% retrieval precision",
        })
    if sub_scores.get("navigation", 100) < 60:
        out.append({
            "title": "Expand sitemap.xml to cover all important pages",
            "category": "navigation",
            "priority": "P1",
            "description": (
                "Your sitemap doesn't cover the internal-link graph. Make sure "
                "every page you want AI assistants to find is listed."
            ),
            "why": "AI crawlers prefer sitemaps for fast, complete discovery.",
            "code_example": (
                "<url><loc>https://example.com/guide</loc>"
                "<lastmod>2026-01-01</lastmod></url>"
            ),
            "estimated_time": "1 hour",
            "estimated_impact": "+20% crawl coverage",
        })
    if sub_scores.get("chunking", 100) < 60 and not any(
        a["category"] == "chunking" for a in out
    ):
        out.append({
            "title": "Tighten paragraph structure for clearer chunks",
            "category": "chunking",
            "priority": "P2",
            "description": (
                "Long, unstructured paragraphs make it hard for an AI to "
                "extract self-contained answers. Aim for 80–600 char paragraphs "
                "grouped under clear sub-headings."
            ),
            "why": "Self-contained chunks are what retrieval systems actually return.",
            "code_example": "<h2>Topic</h2>\n<p>Self-contained answer in 1–3 sentences.</p>",
            "estimated_time": "2 hours",
            "estimated_impact": "+15% snippet eligibility",
        })
    if not out:
        out.append({
            "title": "Keep monitoring AI readability over time",
            "category": "structure",
            "priority": "P2",
            "description": (
                "No critical issues detected at this audit. Re-run after major "
                "content updates to catch regressions early."
            ),
            "why": "AI ingestion quality drifts as content templates change.",
            "code_example": None,
            "estimated_time": "—",
            "estimated_impact": "Stable AI readability",
        })
    out.sort(key=lambda a: (0 if a["priority"] == "P1" else 1, a["category"]))
    return out
