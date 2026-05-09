"""Tests for agents/ai_readability.py — the LLM-readability layer.

Covers (a) the chunker on a known HTML fixture, (b) deterministic action
points when no LLM key is configured, (c) sub-score aggregation across
pages, (d) overall score weighting.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from agents import ai_readability as ar


def _fake_page(**overrides: Any) -> Dict[str, Any]:
    """Build a page dict with the same shape as ``PageReport.to_dict()`` so
    tests don't drag in the full crawler."""
    base = {
        "url": "https://example.com/",
        "status_code": 200,
        "title": "Example Marketing Page That Sells SaaS Tools",
        "title_length": 44,
        "meta_description": "A demo page for testing the AI-readability chunker and its scoring logic.",
        "meta_description_length": 72,
        "h1_count": 1,
        "h2_count": 3,
        "canonical": "https://example.com/",
        "has_open_graph": True,
        "has_schema": True,
        "has_lang": True,
        "images_total": 4,
        "images_missing_alt": 0,
        "internal_links": 12,
    }
    base.update(overrides)
    return base


HTML_FIXTURE = """
<html lang="en"><head>
  <title>Example Marketing Page That Sells SaaS Tools</title>
  <meta name="description" content="A demo page for testing the AI-readability chunker and its scoring logic.">
</head><body>
  <main>
    <h1>Reduce SaaS churn with proactive onboarding</h1>
    <p>Customer success teams that intervene in the first 30 days see roughly 23% lower churn than teams that wait for users to ask for help. The signal is consistent across SMB and mid-market SaaS.</p>
    <section>
      <h2>How proactive onboarding works</h2>
      <p>Reach out to new accounts within 24 hours, identify the activation event for that segment, and confirm completion before day 7. Teams that miss the activation window struggle to recover engagement later.</p>
      <ul>
        <li>Day 0: send a welcome email with a single concrete first step.</li>
        <li>Day 3: review whether the activation event has been hit.</li>
        <li>Day 7: book a 15-minute call if it has not.</li>
      </ul>
    </section>
    <section>
      <h2>Common pitfalls</h2>
      <p>The most common mistake is waiting for the customer to signal a problem. By that point, the renewal conversation is already harder.</p>
    </section>
  </main>
</body></html>
"""


# ── Chunker ──────────────────────────────────────────────────────────────────

def test_chunker_extracts_headings_paragraphs_and_lists():
    chunks = ar._chunk_html(HTML_FIXTURE)
    assert len(chunks) >= 5
    types = {c.type for c in chunks}
    assert "heading_block" in types
    assert "paragraph" in types
    assert "list" in types
    h1_chunks = [c for c in chunks if c.type == "heading_block" and "churn" in c.text.lower()]
    assert h1_chunks, "expected the H1 to be captured as a heading_block chunk"


def test_chunker_redundancy_detection_flags_near_duplicates():
    duplicate_html = HTML_FIXTURE + (
        "<p>Customer success teams that intervene in the first 30 days see "
        "roughly 23% lower churn than teams that wait for users to ask "
        "for help.</p>"
    )
    chunks = ar._chunk_html(duplicate_html)
    redundant = [c for c in chunks if c.is_redundant]
    assert redundant, "near-duplicate paragraph should be flagged as redundant"


def test_chunker_skips_short_noise():
    noise = "<html><body><p>OK</p><p>Yes.</p></body></html>"
    chunks = ar._chunk_html(noise)
    assert chunks == [], "tiny chunks below the min length should be dropped"


# ── Sub-score / overall computation ──────────────────────────────────────────

def test_overall_score_is_weighted_average_of_sub_scores():
    sub = {
        "structure": 80,
        "metadata": 90,
        "chunking": 60,
        "semantics": 70,
        "navigation": 50,
    }
    overall = ar._weighted_overall(sub)
    # Manual: 80*20 + 90*20 + 60*25 + 70*20 + 50*15 = 1600+1800+1500+1400+750 = 7050
    # 7050 / 100 = 70.5 → round to 71
    assert overall == 71


def test_aggregate_sub_scores_means_per_key():
    pp = [
        {"structure": 80, "metadata": 60, "chunking": 70, "semantics": 50, "navigation": 40},
        {"structure": 60, "metadata": 80, "chunking": 50, "semantics": 70, "navigation": 60},
    ]
    out = ar._aggregate_sub_scores(pp)
    assert out["structure"] == 70
    assert out["metadata"] == 70
    assert out["chunking"] == 60
    assert out["semantics"] == 60
    assert out["navigation"] == 50


# ── Deterministic fallback (no LLM) ──────────────────────────────────────────

def test_fallback_action_points_emits_alt_text_fix_when_images_missing_alt():
    sub = {"structure": 80, "metadata": 80, "chunking": 80, "semantics": 60, "navigation": 80}
    issues = {
        "pages_audited": 3,
        "total_images": 20,
        "images_missing_alt": 8,
        "pages_missing_meta_desc": 0,
        "pages_missing_canonical": 0,
        "pages_missing_schema": 0,
        "pages_missing_open_graph": 0,
        "pages_missing_h1": 0,
        "total_chunks_scored": 30,
        "redundant_chunks": 0,
        "low_clarity_chunks": 0,
    }
    aps = ar._fallback_action_points(sub, issues)
    assert any("alt text" in (a.get("title") or "").lower() for a in aps)
    # Priorities are P1 first, then P2
    priorities = [a["priority"] for a in aps]
    assert priorities == sorted(priorities, key=lambda p: 0 if p == "P1" else 1)


def test_fallback_action_points_returns_at_least_one_when_clean():
    sub = {k: 90 for k in ar.SUB_SCORE_WEIGHTS}
    issues = {
        "pages_audited": 1, "total_images": 0, "images_missing_alt": 0,
        "pages_missing_meta_desc": 0, "pages_missing_canonical": 0,
        "pages_missing_schema": 0, "pages_missing_open_graph": 0,
        "pages_missing_h1": 0, "total_chunks_scored": 5, "redundant_chunks": 0,
        "low_clarity_chunks": 0,
    }
    aps = ar._fallback_action_points(sub, issues)
    assert len(aps) >= 1


# ── End-to-end (no LLM) ──────────────────────────────────────────────────────

def test_score_pages_runs_end_to_end_without_llm_key():
    page = _fake_page()
    out = asyncio.run(ar.score_pages(
        [page],
        {page["url"]: HTML_FIXTURE},
        sitemap_url_count=10,
        has_sitemap_xml=True,
        anthropic_key=None,
    ))
    assert out["overall_score"] is not None
    assert 0 <= out["overall_score"] <= 100
    assert set(out["sub_scores"].keys()) == set(ar.SUB_SCORE_WEIGHTS.keys())
    assert isinstance(out["action_points"], list)
    assert out["page_analyses"], "should have at least one page analysis"
    chunks = out["page_analyses"][0]["chunks"]
    assert chunks
    # Without LLM, semantic_clarity stays None; the rest of the chunk shape
    # still has to be intact for the dashboard to render it.
    for c in chunks:
        assert "id" in c and "type" in c and "text" in c


def test_score_pages_handles_empty_input_gracefully():
    out = asyncio.run(ar.score_pages([], {}, anthropic_key=None))
    assert out["overall_score"] is None
    assert out["page_analyses"] == []


def test_score_pages_skips_pages_without_html():
    page = _fake_page(url="https://example.com/no-html")
    out = asyncio.run(ar.score_pages(
        [page], {}, anthropic_key=None,
    ))
    assert out["overall_score"] is None
    assert "skipped_reason" in out


def test_select_pages_picks_homepage_then_most_linked():
    pages = [
        {"url": "https://example.com/about", "status_code": 200, "internal_links": 5},
        {"url": "https://example.com/", "status_code": 200, "internal_links": 99},
        {"url": "https://example.com/blog/x", "status_code": 200, "internal_links": 12},
        {"url": "https://example.com/blog/y", "status_code": 200, "internal_links": 8},
    ]
    selected = ar._select_pages(pages)
    assert [p["url"] for p in selected] == [
        "https://example.com/",
        "https://example.com/blog/x",
        "https://example.com/about",
    ]
