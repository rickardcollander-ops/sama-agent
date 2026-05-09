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

from api.routes.analysis import _filter_run_payload_to_saved


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
