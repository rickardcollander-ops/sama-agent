"""
audit_brief — distil a stored site_audit run into a token-efficient bundle
the tech agent prompts Claude with.

The full audit payload can easily exceed 100 KB on a 200-page crawl. We
can't shove that into a Claude prompt without (a) burning tokens and
(b) drowning the model in noise. ``build_audit_brief`` picks the most
"talkable" pieces: a handful of high-issue pages with their actual
current head/og/JSON-LD snippets, the most severe findings, and the
site-wide meta files.

The shape is deliberately stable — the tech agent's prompt references
specific keys (``brief.pages[].head_html_excerpt`` etc.), so renaming
fields here breaks the suggestion quality silently. Add new fields,
don't rename old ones.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# Hard caps so the brief stays small even when the audit is huge.
MAX_PAGES_DEFAULT = 8
MAX_FINDINGS = 6
MAX_BROKEN_LINKS = 10
MAX_HEAD_HTML_CHARS = 1200
MAX_BODY_SAMPLE_CHARS = 600
MAX_JSONLD_CHARS = 800
MAX_ROBOTS_CHARS = 1024
MAX_FINDING_EXAMPLES = 5


def build_audit_brief(
    run: Dict[str, Any],
    focus: Optional[str] = None,
    max_pages: int = MAX_PAGES_DEFAULT,
) -> Dict[str, Any]:
    """Return a compact summary of a site_audit run for prompt input.

    ``run`` is the full payload as stored in ``site_audits.payload``.
    ``focus`` is the optional area-of-interest tag the user picked
    ("seo", "performance", etc.); we don't filter on it but the tech
    agent passes it through to Claude separately.
    ``max_pages`` controls how many representative pages we include —
    8 keeps the prompt under ~5K input tokens with room for few-shots.
    """
    pages = run.get("pages") or []
    summary = run.get("summary") or {}
    findings = run.get("findings") or []
    broken = run.get("broken_links") or []

    return {
        "domain": run.get("domain"),
        "base_url": run.get("base_url"),
        "scores": run.get("scores") or {},
        "site_meta": _site_meta(run, summary),
        "top_findings": _top_findings(findings),
        "pages": _pick_pages(pages, max_pages),
        "broken_links": [_compact_broken_link(b) for b in broken[:MAX_BROKEN_LINKS]],
        "focus": focus,
    }


def _site_meta(run: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    """Site-wide signals the tech agent uses to propose root-level fixes
    (robots.txt, sitemap, llms.txt, security headers)."""
    robots_txt = run.get("robots_txt_content")
    return {
        "has_robots_txt": bool(summary.get("has_robots_txt")),
        "has_sitemap_xml": bool(summary.get("has_sitemap_xml")),
        "has_llms_txt": bool(summary.get("has_llms_txt")),
        "https": bool(summary.get("https")),
        "robots_txt_content": (robots_txt or "")[:MAX_ROBOTS_CHARS] or None,
        "sitemap_sample": (run.get("sitemap_sample") or [])[:10],
        # Average response time + broken-link count are useful signals for
        # performance-flavoured suggestions without needing the full pages list.
        "avg_response_ms": summary.get("avg_response_ms"),
        "broken_links_count": summary.get("broken_links_count"),
    }


def _top_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pick the most impactful findings — drop healthy ('success') ones,
    sort by severity, and trim per-URL examples so the brief stays tight."""
    sev_order = {"critical": 0, "warning": 1, "info": 2, "success": 3}
    actionable = [f for f in findings if f.get("severity") != "success"]
    actionable.sort(
        key=lambda f: (
            sev_order.get(f.get("severity", "info"), 9),
            -int(f.get("affected_pages") or 0),
        )
    )
    out = []
    for f in actionable[:MAX_FINDINGS]:
        out.append({
            "title": f.get("title"),
            "category": f.get("category"),
            "severity": f.get("severity"),
            "affected_pages": f.get("affected_pages"),
            "examples": (f.get("examples") or [])[:MAX_FINDING_EXAMPLES],
            "how_to_fix": f.get("how_to_fix"),
            "impact": f.get("impact"),
            "effort": f.get("effort"),
        })
    return out


def _pick_pages(pages: List[Dict[str, Any]], max_pages: int) -> List[Dict[str, Any]]:
    """Pick the pages the tech agent should anchor suggestions to.

    Strategy: prefer the homepage (or first sitemap entry), then pages with
    the most issues — those are the ones with the most fixable ground.
    """
    if not pages:
        return []

    # Homepage / shortest-path URL first so the tech agent always has a
    # canonical "main" page to talk about.
    sorted_pages = sorted(
        pages,
        key=lambda p: (
            len((p.get("url") or "").rstrip("/")),  # shorter URL = closer to root
            -len(p.get("issues") or []),  # then by issue count desc
        ),
    )
    # Also include pages with many issues even if they're deep paths.
    by_issues = sorted(
        pages,
        key=lambda p: -len(p.get("issues") or []),
    )

    picked: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for src in (sorted_pages, by_issues):
        for p in src:
            url = p.get("url")
            if not url or url in seen_urls:
                continue
            picked.append(p)
            seen_urls.add(url)
            if len(picked) >= max_pages:
                return [_compact_page(p) for p in picked]
    return [_compact_page(p) for p in picked]


def _compact_page(p: Dict[str, Any]) -> Dict[str, Any]:
    """Strip a PageReport dict to the fields the tech agent prompt needs.

    The full dict carries scoring counters (h2_count, internal_links, …)
    that don't help the model write a snippet. We keep the strings the
    model can actually quote back at the user.
    """
    head = p.get("head_html_excerpt") or ""
    body = p.get("body_text_sample") or ""
    jsonld_blocks = p.get("jsonld_blocks") or []
    return {
        "url": p.get("url"),
        "status_code": p.get("status_code"),
        "title": p.get("title"),
        "title_length": p.get("title_length"),
        "meta_description": p.get("meta_description"),
        "meta_description_length": p.get("meta_description_length"),
        "h1_text": p.get("h1_text"),
        "h2_texts": (p.get("h2_texts") or [])[:5],
        "canonical": p.get("canonical"),
        "schema_types": p.get("schema_types") or [],
        "og_tags": p.get("og_tags") or {},
        "viewport_content": p.get("viewport_content"),
        "html_lang": p.get("html_lang"),
        "head_html_excerpt": head[:MAX_HEAD_HTML_CHARS] if head else None,
        # Only ship the first JSON-LD block — adding more rarely helps the
        # model and triples the cost of these heavy strings.
        "jsonld_first": (jsonld_blocks[0][:MAX_JSONLD_CHARS] if jsonld_blocks else None),
        "body_text_sample": body[:MAX_BODY_SAMPLE_CHARS] if body else None,
        "issues": p.get("issues") or [],
        "word_count": p.get("word_count"),
        "images_total": p.get("images_total"),
        "images_missing_alt": p.get("images_missing_alt"),
    }


def _compact_broken_link(bl: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "url": bl.get("url"),
        "status_code": bl.get("status_code"),
        "found_on": (bl.get("found_on") or [])[:2],
    }
