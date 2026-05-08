"""
Regression tests for the "only what you put in AI Assistant gets measured"
guarantee.

Covers:
  * TenantConfig.language: explicit setting wins, otherwise TLD-inferred,
    otherwise English. The Swedish failure mode was a tenant on
    ``vexdigitalt.se`` getting English Insights queries because
    AnalysisAgent.generate_queries ran without any locale signal.
  * AnalysisAgent._fallback_queries respects the tenant language so the
    deterministic fallback never silently regresses to English.
  * AIVisibilityAgent._build_prompt_categories returns *only* saved
    geo_queries for any real tenant — no fallback to auto-generated or
    hardcoded customer-success English prompts.
  * AIVisibilityAgent.run_monitoring short-circuits cleanly when nothing
    is saved instead of running default prompts.
"""

import pytest

from agents.ai_visibility import AIVisibilityAgent, MONITORING_PROMPTS
from agents.analysis import AnalysisAgent
from shared.tenant import TenantConfig


# ── TenantConfig.language ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({"domain": "vexdigitalt.se"}, "sv"),
        ({"domain": "example.com"}, "en"),
        ({"domain": "example.de"}, "de"),
        ({"domain": "https://www.example.no/x"}, "nb"),
        ({"domain": "WWW.EXAMPLE.DK"}, "da"),
        ({"domain": "subdomain.example.fi"}, "fi"),
        ({"content_language": "fi", "domain": "example.com"}, "fi"),
        ({}, "en"),
    ],
)
def test_tenant_language_resolution(settings, expected):
    cfg = TenantConfig("t", settings)
    assert cfg.language == expected


# ── AnalysisAgent._fallback_queries ──────────────────────────────────────────


def test_fallback_queries_swedish_for_se_domain():
    cfg = TenantConfig(
        "t",
        {
            "domain": "vexdigitalt.se",
            "brand_name": "VexDigitalt",
            "target_audience": "småföretag",
            "category": "företagssystem",
            "competitors": ["Monday.com"],
        },
    )
    agent = AnalysisAgent(tenant_config=cfg)
    queries = agent._fallback_queries(10)

    assert agent.language == "sv"
    assert queries, "fallback should produce queries"
    # Swedish hallmarks — at least one localised template must fire.
    joined = " ".join(queries).lower()
    assert any(token in joined for token in ("bästa", "topp", "konkurrenter", "prisjämförelse"))
    # English hallmarks must not leak into Swedish output.
    assert "best alternatives to" not in joined
    assert "vs competitors" not in joined


def test_fallback_queries_english_default():
    cfg = TenantConfig("t", {"domain": "example.com"})
    agent = AnalysisAgent(tenant_config=cfg)
    queries = agent._fallback_queries(5)
    assert agent.language == "en"
    assert all(isinstance(q, str) and q for q in queries)
    assert any("Best" in q or "Top" in q for q in queries)


def test_fallback_queries_strip_brand():
    cfg = TenantConfig(
        "t",
        {
            "domain": "vexdigitalt.se",
            "brand_name": "VexDigitalt",
            "competitors": ["VexDigitalt"],  # try to inject brand via competitor
        },
    )
    agent = AnalysisAgent(tenant_config=cfg)
    queries = agent._fallback_queries(10)
    # _strip_brand_queries must drop anything that names the brand or domain.
    for q in queries:
        assert "vexdigitalt" not in q.lower()


# ── AIVisibilityAgent._build_prompt_categories ───────────────────────────────


def test_build_prompts_uses_only_saved_queries():
    cfg = TenantConfig(
        "t",
        {
            "domain": "vexdigitalt.se",
            "brand_name": "VexDigitalt",
            "geo_queries": ["Bästa CRM för småföretag", "Vad kostar Monday.com"],
        },
    )
    agent = AIVisibilityAgent(tenant_config=cfg)
    categories = agent._build_prompt_categories()

    assert categories == {
        "user_query": ["Bästa CRM för småföretag", "Vad kostar Monday.com"]
    }


def test_build_prompts_returns_empty_when_no_saved_queries():
    """A real tenant with no geo_queries must NOT fall back to defaults.

    This is the core invariant the user asked for: nothing gets measured
    that wasn't entered in AI Assistant.
    """
    cfg = TenantConfig(
        "t",
        {"domain": "vexdigitalt.se", "brand_name": "VexDigitalt"},
    )
    agent = AIVisibilityAgent(tenant_config=cfg)
    assert agent._build_prompt_categories() == {}


def test_build_prompts_legacy_default_tenant_keeps_monitoring_prompts():
    """The singleton (no tenant_config) still emits the legacy English
    monitoring prompts so internal smoke tests keep working."""
    agent = AIVisibilityAgent(tenant_config=None)
    categories = agent._build_prompt_categories()
    assert set(categories) == set(MONITORING_PROMPTS)


def test_build_prompts_strips_brand_from_saved_queries():
    cfg = TenantConfig(
        "t",
        {
            "domain": "vexdigitalt.se",
            "brand_name": "VexDigitalt",
            # User-saved queries that accidentally include the brand are
            # filtered, since asking ChatGPT "best VexDigitalt alternative"
            # biases the response.
            "geo_queries": [
                "Bästa CRM för småföretag",
                "VexDigitalt vs Monday.com",
            ],
        },
    )
    agent = AIVisibilityAgent(tenant_config=cfg)
    categories = agent._build_prompt_categories()
    assert categories == {"user_query": ["Bästa CRM för småföretag"]}


# ── run_monitoring short-circuit ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_monitoring_skips_when_no_saved_queries():
    cfg = TenantConfig(
        "t",
        {"domain": "vexdigitalt.se", "brand_name": "VexDigitalt"},
    )
    agent = AIVisibilityAgent(tenant_config=cfg)
    result = await agent.run_monitoring()
    assert result["checks_run"] == 0
    assert result["mention_rate"] is None
    assert result["skipped_reason"] == "no_saved_queries"
