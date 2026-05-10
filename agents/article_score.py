"""
Heuristic article scoring (0-100) used by the premium article writer.

Pure-Python — no LLM calls, no network. Runs on the structured payload the
writer assembles so the score is deterministic and free.

The breakdown intentionally mirrors what the dashboard's right-hand panel
displays (word count, keywords, images, internal/external links, etc.).
Tuning these weights in one place keeps backend output and frontend badges
in sync.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# Weights sum to 100. Adjust here, not in callers.
_WEIGHTS = {
    "word_count": 15,
    "headings": 10,
    "table_of_contents": 10,
    "key_takeaways": 10,
    "images": 10,
    "internal_links": 10,
    "external_links": 10,
    "meta_description": 10,
    "keyword_density": 10,
    "faq": 5,
}


def _word_count(markdown: str) -> int:
    return len(re.findall(r"\b\w+\b", markdown or ""))


def _heading_count(markdown: str, level: int = 2) -> int:
    pattern = rf"^{'#' * level}\s+\S"
    return len(re.findall(pattern, markdown or "", flags=re.MULTILINE))


def _keyword_density(markdown: str, keyword: str) -> float:
    if not keyword:
        return 0.0
    words = _word_count(markdown)
    if words == 0:
        return 0.0
    hits = len(re.findall(re.escape(keyword.lower()), (markdown or "").lower()))
    return (hits / words) * 100.0


def _md_link_count(markdown: str, internal_domain: str | None = None) -> tuple[int, int]:
    """Return (internal, external) link counts in the rendered markdown.

    A link is "internal" if its href is relative (starts with ``/``) or its
    host matches ``internal_domain``. Everything else counts as external.
    Anchors-only links (``#section``) are excluded — they're TOC noise.
    """
    internal = 0
    external = 0
    for match in re.finditer(r"\[[^\]]+\]\(([^)\s]+)", markdown or ""):
        href = match.group(1)
        if href.startswith("#"):
            continue
        if href.startswith("/") or (internal_domain and internal_domain in href):
            internal += 1
        else:
            external += 1
    return internal, external


def compute_article_score(
    *,
    markdown: str,
    meta_description: str = "",
    primary_keyword: str = "",
    table_of_contents: List[Any] | None = None,
    key_takeaways: List[Any] | None = None,
    faq: List[Any] | None = None,
    image_count: int = 0,
    internal_domain: str | None = None,
) -> Dict[str, Any]:
    """Score a structured article and return both the total and a per-rule
    breakdown the dashboard can show as "optimization suggestions".
    """
    words = _word_count(markdown)
    h2 = _heading_count(markdown, level=2)
    internal_links, external_links = _md_link_count(markdown, internal_domain)
    density = _keyword_density(markdown, primary_keyword)
    meta_len = len((meta_description or "").strip())

    rules: List[Dict[str, Any]] = []

    def _add(key: str, ok: bool, message: str, *, partial: float | None = None) -> None:
        weight = _WEIGHTS[key]
        earned = weight if ok else (round(weight * partial) if partial is not None else 0)
        rules.append({
            "key": key,
            "label": message,
            "ok": ok,
            "weight": weight,
            "earned": earned,
        })

    _add("word_count", words >= 1500, f"Word count {words} (target >= 1500)",
         partial=min(1.0, words / 1500.0))
    _add("headings", h2 >= 3, f"{h2} H2 sections (target >= 3)",
         partial=min(1.0, h2 / 3.0))
    _add("table_of_contents", bool(table_of_contents and len(table_of_contents) >= 3),
         "Table of contents present")
    _add("key_takeaways", bool(key_takeaways and len(key_takeaways) >= 4),
         "Key takeaways table populated")
    _add("images", image_count >= 3, f"{image_count} images (target >= 3)",
         partial=min(1.0, image_count / 3.0))
    _add("internal_links", internal_links >= 5, f"{internal_links} internal links (target >= 5)",
         partial=min(1.0, internal_links / 5.0))
    _add("external_links", external_links >= 3, f"{external_links} external links (target >= 3)",
         partial=min(1.0, external_links / 3.0))
    _add("meta_description", 130 <= meta_len <= 160,
         f"Meta description {meta_len} chars (target 130-160)")
    _add("keyword_density", 0.5 <= density <= 2.0,
         f"Primary keyword density {density:.2f}% (target 0.5-2.0%)")
    _add("faq", bool(faq and len(faq) >= 3), "FAQ section present")

    total = sum(r["earned"] for r in rules)
    suggestions = [r["label"] for r in rules if not r["ok"]]

    return {
        "score": int(total),
        "rules": rules,
        "suggestions": suggestions,
        "metrics": {
            "word_count": words,
            "h2_count": h2,
            "internal_links": internal_links,
            "external_links": external_links,
            "image_count": image_count,
            "meta_description_length": meta_len,
            "keyword_density_pct": round(density, 2),
        },
    }
