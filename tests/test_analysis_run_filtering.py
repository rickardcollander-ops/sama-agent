"""
Read-time filtering of analysis_runs.payload to currently-saved geo_queries.

A run that was persisted before the user trimmed (or replaced) their saved
AI Assistant queries can still hold rows for prompts that are no longer
tracked. The Insights "Per fråga" tab reads the raw payload, so without a
read-time clamp those orphaned queries reappear and contradict the
"only what you put in AI Assistant gets measured" guarantee.

These tests cover the helpers in api/routes/analysis.py that strip
mismatched query_results and rebuild the overview aggregates.
"""

from api.routes.analysis import (
    _augment_run_payload_with_checks,
    _build_query_result_from_checks,
    _filter_run_payload_to_saved,
)


def _make_query(query, mentioned=False, seo_rank=None, gap="both_losers"):
    return {
        "query": query,
        "seo_rank": seo_rank,
        "seo_competitors_in_top10": 0,
        "ai_results": [
            {"platform": "chatgpt", "mentioned": mentioned, "rank": None,
             "cited_as_source": False, "sentiment": None, "competitors_mentioned": []},
        ],
        "gap": gap,
    }


def _payload(*queries):
    return {
        "id": "run-1",
        "status": "completed",
        "platforms": ["chatgpt"],
        "query_results": list(queries),
        "overview": {
            "overall_mention_rate": 0.5,
            "seo_top10_coverage": 0.5,
            "queries_with_presence": 99,
            "total_queries": 99,
            "top_opportunities": [{"query": "stale", "reason": "stale"}],
        },
    }


def test_filter_drops_queries_not_in_saved_set():
    payload = _payload(
        _make_query("best tools for managing business operations in 2024"),
        _make_query("Bästa CRM för småföretag", mentioned=True),
    )
    out = _filter_run_payload_to_saved(payload, ["Bästa CRM för småföretag"])
    assert [q["query"] for q in out["query_results"]] == ["Bästa CRM för småföretag"]


def test_filter_recomputes_overview_on_subset():
    payload = _payload(
        _make_query("best tools for managing business operations in 2024"),
        _make_query("Bästa CRM för småföretag", mentioned=True, seo_rank=4),
    )
    out = _filter_run_payload_to_saved(payload, ["Bästa CRM för småföretag"])
    overview = out["overview"]
    # Single saved query, single platform, 1 mention → 100% mention rate, 100% top10.
    assert overview["total_queries"] == 1
    assert overview["overall_mention_rate"] == 1.0
    assert overview["seo_top10_coverage"] == 1.0
    assert overview["queries_with_presence"] == 1
    # Stale opportunity must be discarded; nothing matches the saved subset here.
    assert overview["top_opportunities"] == []


def test_filter_returns_empty_when_no_query_overlaps():
    payload = _payload(
        _make_query("best tools for managing business operations in 2024"),
        _make_query("top software platforms for small business owners"),
    )
    out = _filter_run_payload_to_saved(payload, ["Bästa CRM för småföretag"])
    assert out["query_results"] == []
    assert out["overview"]["total_queries"] == 0
    assert out["overview"]["overall_mention_rate"] == 0
    assert out["overview"]["top_opportunities"] == []


def test_filter_hides_everything_when_saved_set_is_empty():
    """A tenant who removed all AI Assistant queries should see no historical rows."""
    payload = _payload(_make_query("anything"))
    out = _filter_run_payload_to_saved(payload, [])
    assert out["query_results"] == []
    assert out["overview"]["total_queries"] == 0


def test_filter_match_is_case_and_whitespace_insensitive():
    payload = _payload(_make_query("  Bästa CRM för småföretag "))
    out = _filter_run_payload_to_saved(payload, ["bästa crm för småföretag"])
    assert len(out["query_results"]) == 1


def test_filter_passes_through_when_all_queries_are_still_saved():
    """No-op fast path: payload identity is preserved when nothing changes."""
    payload = _payload(_make_query("Bästa CRM för småföretag"))
    out = _filter_run_payload_to_saved(payload, ["Bästa CRM för småföretag"])
    assert out is payload  # original overview is left intact


def test_filter_tolerates_missing_query_results():
    payload = {"id": "x", "status": "running"}
    out = _filter_run_payload_to_saved(payload, ["whatever"])
    assert out is payload


def test_filter_tolerates_non_dict_payload():
    assert _filter_run_payload_to_saved(None, ["x"]) is None
    assert _filter_run_payload_to_saved("string", ["x"]) == "string"


# ── Augmentation: pull saved-but-missing queries from ai_visibility_checks ──


def _check(prompt, engine, mentioned, rank=None, checked_at="2026-05-09T00:00:00Z",
           competitors=None, sentiment=None):
    return {
        "prompt": prompt,
        "ai_engine": engine,
        "mentioned": mentioned,
        "rank": rank,
        "competitors_mentioned": competitors or [],
        "sentiment": sentiment,
        "checked_at": checked_at,
    }


def test_augment_adds_missing_saved_query_from_checks():
    payload = _payload()  # no query_results yet
    payload["platforms"] = ["chatgpt", "claude", "google_aio"]
    checks = [
        _check("Bästa CRM för småföretag", "ChatGPT (GPT-4o)", True, rank=2),
        _check("Bästa CRM för småföretag", "Claude (Anthropic)", False),
    ]
    out = _augment_run_payload_with_checks(payload, ["Bästa CRM för småföretag"], checks)
    rows = out["query_results"]
    assert [r["query"] for r in rows] == ["Bästa CRM för småföretag"]
    by_platform = {r["platform"]: r for r in rows[0]["ai_results"]}
    # platforms in run.platforms get a row each, with placeholders for engines
    # ai_visibility doesn't track (google_aio).
    assert by_platform["chatgpt"]["mentioned"] is True
    assert by_platform["chatgpt"]["rank"] == 2
    assert by_platform["claude"]["mentioned"] is False
    assert by_platform["google_aio"]["mentioned"] is False
    assert by_platform["google_aio"]["rank"] is None
    # SEO data isn't carried by ai_visibility_checks.
    assert rows[0]["seo_rank"] is None


def test_augment_leaves_existing_query_alone():
    payload = _payload(_make_query("Bästa CRM för småföretag", mentioned=True))
    payload["platforms"] = ["chatgpt"]
    checks = [_check("Bästa CRM för småföretag", "ChatGPT (GPT-4o)", False)]
    out = _augment_run_payload_with_checks(payload, ["Bästa CRM för småföretag"], checks)
    # existing row preserved verbatim — augmentation never overwrites the
    # original full-analysis snapshot (which carries SEO data the checks
    # table doesn't have).
    assert out is payload


def test_augment_skips_query_with_no_checks():
    payload = _payload()
    payload["platforms"] = ["chatgpt"]
    out = _augment_run_payload_with_checks(payload, ["nothing-checked-yet"], [])
    assert out is payload


def test_augment_uses_latest_check_per_engine():
    payload = _payload()
    payload["platforms"] = ["chatgpt"]
    checks = [
        _check("q", "ChatGPT (GPT-4o)", True, rank=1, checked_at="2026-05-10T10:00:00Z"),
        _check("q", "ChatGPT (GPT-4o)", False, checked_at="2026-05-09T10:00:00Z"),
    ]
    out = _augment_run_payload_with_checks(payload, ["q"], checks)
    row = out["query_results"][0]["ai_results"][0]
    # Newer check wins, even if it appears second in the input list.
    assert row["mentioned"] is True
    assert row["rank"] == 1


def test_augment_recomputes_overview():
    payload = _payload()
    payload["platforms"] = ["chatgpt"]
    payload["overview"] = {
        "overall_mention_rate": 0.0,
        "seo_top10_coverage": 0.0,
        "queries_with_presence": 0,
        "total_queries": 0,
        "top_opportunities": [],
    }
    checks = [_check("q", "ChatGPT (GPT-4o)", True, rank=1)]
    out = _augment_run_payload_with_checks(payload, ["q"], checks)
    overview = out["overview"]
    assert overview["total_queries"] == 1
    assert overview["overall_mention_rate"] == 1.0
    # seo_rank is null for synthesised rows so top10 coverage stays at 0.
    assert overview["seo_top10_coverage"] == 0.0
    assert overview["queries_with_presence"] == 1


def test_augment_ignores_unmapped_engine_names():
    """An engine we don't know how to map (e.g. a renamed model) is dropped
    rather than producing a row with platform=None."""
    payload = _payload()
    payload["platforms"] = ["chatgpt"]
    checks = [_check("q", "Some New Model", True)]
    out = _augment_run_payload_with_checks(payload, ["q"], checks)
    # No mapping → no synthesised row.
    assert out is payload


def test_augment_query_match_is_case_and_whitespace_insensitive():
    payload = _payload()
    payload["platforms"] = ["chatgpt"]
    checks = [_check("  Bästa CRM För Småföretag ", "ChatGPT (GPT-4o)", True)]
    out = _augment_run_payload_with_checks(payload, ["bästa crm för småföretag"], checks)
    assert len(out["query_results"]) == 1


def test_augment_noop_when_nothing_saved():
    payload = _payload(_make_query("q"))
    out = _augment_run_payload_with_checks(payload, [], [])
    assert out is payload


def test_augment_tolerates_non_dict_payload():
    assert _augment_run_payload_with_checks(None, ["q"], []) is None
    assert _augment_run_payload_with_checks("x", ["q"], []) == "x"


def test_build_query_result_returns_none_without_data():
    assert _build_query_result_from_checks("q", ["chatgpt"], []) is None
    # Only unmapped engines → still None.
    assert _build_query_result_from_checks(
        "q", ["chatgpt"], [_check("q", "Unknown Model", True)]
    ) is None
