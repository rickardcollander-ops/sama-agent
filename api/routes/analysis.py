"""
Analysis API routes — SEO + GEO unified visibility (P2.9).

Endpoints (all tenant-scoped via X-Tenant-ID header):
  POST /api/analysis/run              kick off a new analysis (async)
  POST /api/analysis/generate-queries get LLM-suggested queries
  GET  /api/analysis/runs             list persisted runs (history view)
  GET  /api/analysis/runs/{id}        get one run (status polling + replay)

The dashboard's /api/analysis/run Next.js route proxies here when
ANALYSIS_REAL=1 in the deploy env. Until that flag flips it serves
deterministic mock data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_query(q: Any) -> str:
    return (q or "").strip().lower() if isinstance(q, str) else ""


# ai_visibility_checks records the engine under its display name; the
# analysis_runs payload uses the short AIPlatform identifier the dashboard
# matrix renders. Engines without a mapping are skipped when augmenting.
_AI_VISIBILITY_ENGINE_TO_PLATFORM: Dict[str, str] = {
    "ChatGPT (GPT-4o)": "chatgpt",
    "Claude (Anthropic)": "claude",
    "Gemini (Google)": "gemini",
    "Perplexity AI": "perplexity",
    "Microsoft Copilot": "copilot",
}


def _classify_gap_for_augment(
    seo_rank: Optional[int], ai_results: List[Dict[str, Any]]
) -> str:
    """Mirror agents.analysis.AnalysisAgent._classify_gap so synthesised rows
    pick up the same gap labels the matrix already understands. Duplicated
    here to keep this read-time helper free of the agent-stack import (which
    pulls in httpx/anthropic at module load time)."""
    seo_strong = seo_rank is not None and seo_rank <= 10
    mentioned_count = sum(1 for r in ai_results if r.get("mentioned"))
    ai_strong = mentioned_count / max(len(ai_results), 1) >= 0.5
    competitor_strong = any(r.get("competitors_mentioned") for r in ai_results)
    if competitor_strong and not seo_strong and not ai_strong:
        return "competitor_dominates"
    if seo_strong and not ai_strong:
        return "seo_winner_geo_loser"
    if not seo_strong and ai_strong:
        return "geo_winner_seo_loser"
    if seo_strong and ai_strong:
        return "both_winners"
    return "both_losers"


def _build_query_result_from_checks(
    query: str,
    platforms: List[str],
    checks: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Synthesise a query_results entry from ai_visibility_checks rows.

    Used when the run's stored payload doesn't yet have an entry for *query*
    (typically because the user added the prompt to AI Assistant after the
    last full analysis ran). Returns ``None`` when AI Assistant hasn't yet
    produced any checks for the query — the caller drops the row in that
    case so the matrix doesn't grow empty placeholder rows.

    SEO fields are left null because ai_visibility_checks doesn't carry
    Google rank data; the matrix renders "—" for those columns and the gap
    classifier treats the SEO side as not-strong.
    """
    qnorm = _normalize_query(query)
    if not qnorm:
        return None

    # Latest check per platform (engine display-name → AIPlatform mapping).
    by_platform: Dict[str, Dict[str, Any]] = {}
    for c in checks:
        if not isinstance(c, dict):
            continue
        if _normalize_query(c.get("prompt")) != qnorm:
            continue
        platform = _AI_VISIBILITY_ENGINE_TO_PLATFORM.get(c.get("ai_engine") or "")
        if not platform:
            continue
        prev = by_platform.get(platform)
        prev_ts = (prev or {}).get("checked_at") or ""
        cur_ts = c.get("checked_at") or ""
        if prev is None or cur_ts > prev_ts:
            by_platform[platform] = c

    if not by_platform:
        return None

    ai_results: List[Dict[str, Any]] = []
    for p in platforms:
        c = by_platform.get(p)
        if c is not None:
            ai_results.append({
                "platform": p,
                "mentioned": bool(c.get("mentioned")),
                "rank": c.get("rank"),
                "cited_as_source": False,
                "sentiment": c.get("sentiment"),
                "competitors_mentioned": list(c.get("competitors_mentioned") or []),
            })
        else:
            # Platform was tracked by the original run but ai_visibility doesn't
            # cover it (e.g. google_aio). Surface a placeholder so the column
            # still renders side-by-side with the populated ones.
            ai_results.append({
                "platform": p,
                "mentioned": False,
                "rank": None,
                "cited_as_source": False,
                "sentiment": None,
                "competitors_mentioned": [],
            })

    return {
        "query": query,
        "seo_rank": None,
        "seo_competitors_in_top10": 0,
        "ai_results": ai_results,
        "gap": _classify_gap_for_augment(None, ai_results),
    }


def _build_run_payload_from_checks_only(
    tenant_id: str,
    saved_queries: List[str],
    checks: List[Dict[str, Any]],
    brand_name: Optional[str],
    domain: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Synthesise a complete AnalysisRun payload from ai_visibility_checks.

    Used when the tenant has run AI Assistant checks (via /c/geo) but never
    triggered a full /api/analysis/run, so no analysis_runs row exists. The
    Insights matrix would otherwise show its empty state even though the
    monitoring data is sitting in ai_visibility_checks.

    Returns ``None`` when there's nothing meaningful to display — either no
    saved queries, no checks, or no engine in the checks maps to a known
    AIPlatform. Callers fall back to the empty state in that case.
    """
    if not saved_queries or not checks:
        return None

    # Derive platforms from the checks data so the matrix columns line up
    # with what we actually have. Preserve a stable order so the matrix
    # doesn't shuffle columns between renders.
    platform_order = ["chatgpt", "claude", "gemini", "perplexity", "copilot"]
    seen_platforms: set[str] = set()
    latest_checked_at = ""
    for c in checks:
        if not isinstance(c, dict):
            continue
        platform = _AI_VISIBILITY_ENGINE_TO_PLATFORM.get(c.get("ai_engine") or "")
        if platform:
            seen_platforms.add(platform)
        ts = c.get("checked_at") or ""
        if ts > latest_checked_at:
            latest_checked_at = ts
    if not seen_platforms:
        return None
    platforms = [p for p in platform_order if p in seen_platforms]

    query_results: List[Dict[str, Any]] = []
    for q in saved_queries:
        synth = _build_query_result_from_checks(q, platforms, checks)
        if synth is not None:
            query_results.append(synth)

    if not query_results:
        return None

    return {
        "id": f"synth-{tenant_id}",
        "status": "completed",
        "synthetic": True,
        "source": "ai_visibility_checks",
        "tenant_id": tenant_id,
        "brand_name": brand_name,
        "domain": domain,
        "query_count": len(query_results),
        "platform_count": len(platforms),
        "platforms": platforms,
        "query_results": query_results,
        "overview": _rebuild_overview(query_results),
        "completed_at": latest_checked_at or None,
    }


def _load_recent_checks(tenant_id: str) -> List[Dict[str, Any]]:
    """Pull the recent ai_visibility_checks rows used by augmentation/synthesis."""
    try:
        sb = get_supabase()
        chk = (
            sb.table("ai_visibility_checks")
            .select(
                "prompt,ai_engine,mentioned,rank,sentiment,"
                "competitors_mentioned,checked_at"
            )
            .eq("tenant_id", tenant_id)
            .order("checked_at", desc=True)
            .limit(500)
            .execute()
        )
        return chk.data or []
    except Exception:
        logger.warning(
            "could not load ai_visibility_checks for tenant %s", tenant_id, exc_info=True
        )
        return []


def _augment_run_payload_with_checks(
    payload: Any,
    saved_queries: List[str],
    checks: List[Dict[str, Any]],
) -> Any:
    """Add synthesised query_results entries for saved AI Assistant prompts
    that aren't yet in the run's stored payload.

    The Insights matrix used to go blank whenever the user changed their
    AI Assistant queries between full analyses: the read-time filter dropped
    the run's old prompts, but nothing populated the new ones until the next
    on-demand /api/analysis/run finished. This helper plugs the gap by
    reading the running totals out of ai_visibility_checks (which the AI
    Assistant page already keeps fresh) and patching them onto the matrix.

    Run after :func:`_filter_run_payload_to_saved`. The combination keeps
    the "only what's saved is shown" invariant intact while making sure
    every currently-saved query has a row as long as we have any check data
    for it.
    """
    if not isinstance(payload, dict):
        return payload
    if not saved_queries or not checks:
        return payload

    existing_results = payload.get("query_results")
    if not isinstance(existing_results, list):
        return payload

    have_norm = {
        _normalize_query(q.get("query"))
        for q in existing_results
        if isinstance(q, dict)
    }
    platforms = payload.get("platforms")
    if not isinstance(platforms, list):
        platforms = []
    platforms = [p for p in platforms if isinstance(p, str)]

    additions: List[Dict[str, Any]] = []
    for q in saved_queries:
        norm = _normalize_query(q)
        if not norm or norm in have_norm:
            continue
        synth = _build_query_result_from_checks(q, platforms, checks)
        if synth is not None:
            additions.append(synth)

    if not additions:
        return payload

    out = dict(payload)
    out["query_results"] = list(existing_results) + additions
    out["overview"] = _rebuild_overview(out["query_results"])
    return out


def _filter_run_payload_to_saved(
    payload: Dict[str, Any], saved_queries: List[str]
) -> Dict[str, Any]:
    """Restrict a stored AnalysisRun payload to currently-saved AI Assistant queries.

    Older runs may carry query_results for prompts the user has since removed
    from AI Assistant (or that were measured before the saved-only invariant
    was enforced). Surfacing those would contradict the "only what you put in
    AI Assistant gets measured" guarantee, so we drop them at read time and
    rebuild the overview aggregates on the filtered subset to keep the
    headline numbers consistent with the table.
    """
    if not isinstance(payload, dict):
        return payload

    results = payload.get("query_results")
    if not isinstance(results, list):
        return payload

    saved_norm = {_normalize_query(q) for q in saved_queries if _normalize_query(q)}
    filtered = [
        q for q in results
        if isinstance(q, dict) and _normalize_query(q.get("query")) in saved_norm
    ]

    if len(filtered) == len(results):
        return payload

    out = dict(payload)
    out["query_results"] = filtered
    out["overview"] = _rebuild_overview(filtered)
    return out


def _rebuild_overview(query_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mirror agents.analysis.AnalysisAgent._build_overview for filtered subsets."""
    total_queries = len(query_results)
    platform_count = max(
        (len(q.get("ai_results") or []) for q in query_results), default=0
    )
    total_slots = total_queries * platform_count

    total_mentions = sum(
        sum(1 for r in (q.get("ai_results") or []) if r.get("mentioned"))
        for q in query_results
    )
    seo_top10 = sum(
        1 for q in query_results
        if q.get("seo_rank") and q["seo_rank"] <= 10
    )
    present = sum(
        1 for q in query_results
        if (q.get("seo_rank") and q["seo_rank"] <= 10)
        or any(r.get("mentioned") for r in (q.get("ai_results") or []))
    )

    opportunities = [
        {
            "query": q.get("query", ""),
            "reason": (
                "You rank on Google but AIs don't mention you — citation gap"
                if q.get("gap") == "seo_winner_geo_loser"
                else "AIs mention you but Google doesn't rank you — backlink/pillar gap"
            ),
        }
        for q in query_results
        if q.get("gap") in ("seo_winner_geo_loser", "geo_winner_seo_loser")
    ][:3]

    return {
        "overall_mention_rate": (total_mentions / total_slots) if total_slots else 0,
        "seo_top10_coverage": (seo_top10 / total_queries) if total_queries else 0,
        "queries_with_presence": present,
        "total_queries": total_queries,
        "top_opportunities": opportunities,
    }


class GenerateQueriesPayload(BaseModel):
    count: int = 10
    # When ``mode="suggest"`` the endpoint returns LLM-generated suggestions in
    # the tenant's language — used by the AI Assistant page to populate the
    # "suggested queries" picker before the user saves them. The default
    # ``mode="saved"`` returns whatever is already saved under
    # ``tenant_config.geo_queries``: that's what feeds the Insights page, and
    # it must never include unsaved auto-generated prompts because the
    # measurement guarantee is "only what you put in AI Assistant gets
    # measured".
    mode: str = "saved"


class RunPayload(BaseModel):
    # ``queries`` is intentionally optional — the endpoint pulls the queries
    # from saved ``geo_queries`` so the dashboard can't accidentally measure
    # something that was never added in AI Assistant. The field is kept for
    # backward compatibility with existing clients and is silently ignored.
    queries: Optional[List[str]] = None
    platforms: Optional[List[str]] = None
    # Optional overrides — when the user just typed these on the analysis
    # page they should win over whatever's stored in tenant settings.
    brand_name: Optional[str] = None
    domain: Optional[str] = None
    competitors: Optional[List[str]] = None


# ── POST /generate-queries ───────────────────────────────────────────────────

@router.post("/generate-queries")
async def generate_queries(payload: GenerateQueriesPayload, request: Request):
    """Return the queries that drive the Insights page.

    ``mode="saved"`` (default): returns the list saved under
    ``tenant_config.geo_queries`` — exactly what the user added in AI
    Assistant. Nothing else is ever surfaced to Insights.

    ``mode="suggest"``: returns LLM-generated buyer-intent suggestions in the
    tenant's language. The AI Assistant page calls this to show suggestions a
    user can review and explicitly save; suggestions returned here are never
    measured until the user persists them via user_settings.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)
    count = max(1, min(payload.count, 25))

    if payload.mode == "suggest":
        from agents.analysis import AnalysisAgent
        agent = AnalysisAgent(tenant_config=config)
        queries = await agent.generate_queries(count=count)
        return {"queries": queries, "mode": "suggest"}

    saved = list(getattr(config, "geo_queries", []) or [])
    return {"queries": saved[:count], "mode": "saved"}


# ── POST /run ────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_analysis(payload: RunPayload, request: Request):
    """
    Kick off an analysis. Persists a row immediately, runs the orchestration
    in the background, and returns the row id so the dashboard can poll
    /runs/{id}.

    The queries that get measured are *always* the tenant's saved
    ``geo_queries`` (set in AI Assistant). ``payload.queries`` is accepted
    for backward compatibility but ignored — letting clients send arbitrary
    queries here would silently bypass the "only what you put in AI Assistant
    gets measured" guarantee that the rest of the product depends on.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)

    queries = [q.strip() for q in (getattr(config, "geo_queries", []) or []) if q and q.strip()]
    if not queries:
        raise HTTPException(
            status_code=400,
            detail=(
                "No queries configured. Add the prompts you want measured "
                "under AI Assistant before running an analysis."
            ),
        )
    if len(queries) > 25:
        # geo_queries is the source of truth; cap silently so the user never
        # ends up in a state where the run endpoint refuses their saved list.
        queries = queries[:25]

    platforms = payload.platforms or ["chatgpt", "claude", "perplexity", "google_aio"]
    sb = get_supabase()

    # Apply per-request overrides (what the user just typed) over the
    # stored tenant config. This is what gets persisted on the run row
    # AND what the agent uses for crawling/mention detection.
    brand_name = (payload.brand_name or getattr(config, "brand_name", None) or "").strip() or None
    domain = (payload.domain or getattr(config, "domain", None) or "").strip() or None
    competitors = payload.competitors if payload.competitors is not None else (
        list(getattr(config, "competitors", []) or [])
    )

    # Persist the row before kicking off the background task — if the insert
    # fails (RLS, missing table, bad credentials) the dashboard has no id to
    # poll, so surface the real reason instead of returning {"id": null}.
    try:
        ins = sb.table("analysis_runs").insert({
            "tenant_id": tenant_id,
            "brand_name": brand_name,
            "domain": domain,
            "query_count": len(queries),
            "platform_count": len(platforms),
            "status": "running",
        }).execute()
    except Exception as e:
        logger.exception("Could not insert analysis_runs row")
        raise HTTPException(
            status_code=500,
            detail=f"Could not create analysis run row: {e}",
        )

    if not ins.data:
        raise HTTPException(
            status_code=500,
            detail="analysis_runs insert returned no row (check RLS policy for analysis_runs)",
        )
    run_id = ins.data[0]["id"]

    asyncio.create_task(_execute_analysis(
        run_id, tenant_id, queries, platforms, brand_name, domain, competitors,
    ))

    return {"id": run_id, "status": "running"}


async def _execute_analysis(
    run_id: str,
    tenant_id: str,
    queries: List[str],
    platforms: List[str],
    brand_name: Optional[str] = None,
    domain: Optional[str] = None,
    competitors: Optional[List[str]] = None,
) -> None:
    """Background task: runs the analysis and updates the row."""
    from agents.analysis import AnalysisAgent
    sb = get_supabase()
    config = await get_tenant_config(tenant_id)
    agent = AnalysisAgent(tenant_config=config)
    if brand_name:
        agent.brand_name = brand_name
    if domain:
        agent.domain = domain
    if competitors is not None:
        agent.competitors = list(competitors)

    try:
        result = await agent.run(queries, platforms)
        result["id"] = run_id
        update = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "payload": result,
        }
    except Exception as e:
        logger.exception(f"Analysis run {run_id} failed for tenant {tenant_id}")
        update = {
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }

    try:
        sb.table("analysis_runs").update(update).eq("id", run_id).execute()
    except Exception:
        logger.warning(f"Could not persist analysis_run {run_id} update", exc_info=True)


# ── GET /runs ────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(request: Request, limit: int = 20):
    """Recent analysis runs for this tenant (for the history view)."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("analysis_runs")
            .select("id,brand_name,domain,query_count,platform_count,status,started_at,completed_at,error")
            .eq("tenant_id", tenant_id)
            .order("started_at", desc=True)
            .limit(min(limit, 100))
            .execute()
        )
        return {"runs": result.data or []}
    except Exception as e:
        logger.error(f"list analysis_runs failed: {e}")
        return {"runs": []}


# ── GET /latest ──────────────────────────────────────────────────────────────

@router.get("/latest")
async def get_latest(request: Request) -> Dict[str, Any]:
    """Return the data the Insights matrix should render right now.

    Resolution order:
      1. The most recent completed ``analysis_runs`` row, filtered to the
         tenant's currently-saved ``geo_queries`` and augmented with
         ``ai_visibility_checks`` so freshly-added prompts still appear.
      2. A synthetic payload built purely from ``ai_visibility_checks`` —
         used when the tenant has run AI Assistant checks via /c/geo but
         never triggered /api/analysis/run. Without this fallback the
         matrix went blank for visibility-only tenants even though their
         monitoring data was already in the database.
      3. ``{"status": "empty"}`` when neither source has anything to render.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    try:
        config = await get_tenant_config(tenant_id)
        saved = list(getattr(config, "geo_queries", []) or [])
        brand_name = getattr(config, "brand_name", None)
        domain = getattr(config, "domain", None)
    except Exception:
        logger.warning(
            "tenant config lookup failed in /api/analysis/latest", exc_info=True
        )
        saved, brand_name, domain = [], None, None

    try:
        result = (
            sb.table("analysis_runs")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("status", "completed")
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"latest analysis_runs lookup failed: {e}")
        rows = []

    if rows:
        row = rows[0]
        payload = row.get("payload")
        if isinstance(payload, dict):
            payload["id"] = row["id"]
            payload["status"] = row["status"]
            filtered = _filter_run_payload_to_saved(payload, saved)
            checks = _load_recent_checks(tenant_id) if saved else []
            return _augment_run_payload_with_checks(filtered, saved, checks)

    if saved:
        checks = _load_recent_checks(tenant_id)
        synth = _build_run_payload_from_checks_only(
            tenant_id, saved, checks, brand_name, domain
        )
        if synth is not None:
            return synth

    return {"status": "empty", "synthetic": True, "source": "none"}


# ── GET /runs/{id} ───────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> Dict[str, Any]:
    """One run with its full payload — used for polling and replay.

    Historical runs are clamped to the tenant's currently-saved
    ``geo_queries`` before being returned: prompts that have since been
    removed from AI Assistant should not reappear on Insights, and the
    aggregate overview is recomputed so totals match the visible rows.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("analysis_runs")
            .select("*")
            .eq("id", run_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        row = rows[0]
        # If the run is complete, surface the AnalysisRun payload directly so
        # the frontend can render it without reshaping.
        if row.get("status") == "completed" and row.get("payload"):
            payload = row["payload"]
            payload["id"] = row["id"]
            payload["status"] = row["status"]
            try:
                config = await get_tenant_config(tenant_id)
                saved = list(getattr(config, "geo_queries", []) or [])
            except Exception:
                logger.warning(
                    "could not load tenant config when filtering run %s; returning raw payload",
                    run_id,
                    exc_info=True,
                )
                saved = []
            filtered = _filter_run_payload_to_saved(payload, saved)
            checks = _load_recent_checks(tenant_id) if saved else []
            return _augment_run_payload_with_checks(filtered, saved, checks)
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get analysis_run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
