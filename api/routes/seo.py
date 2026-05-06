import asyncio
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from agents.seo import seo_agent

logger = logging.getLogger(__name__)
from api.routes.seo_chat import router as chat_router

router = APIRouter()
router.include_router(chat_router)


@router.get("/status")
async def get_status():
    """Get SEO agent status"""
    try:
        return {
            "agent": "seo",
            "status": "operational",
            "target_keywords": len(seo_agent.TARGET_KEYWORDS),
            "competitors": seo_agent.COMPETITORS
        }
    except Exception as e:
        return {
            "agent": "seo",
            "status": "operational",
            "target_keywords": 0,
            "competitors": [],
            "error": str(e)
        }


async def _get_tenant_brand(sb, tenant_id: str) -> dict:
    """Load brand context for the given tenant from user_settings."""
    try:
        result = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
        s = (result.data or {}).get("settings", {}) if result.data else {}
        return {
            "brand_name": s.get("brand_name", ""),
            "domain": s.get("domain", ""),
            "brand_description": s.get("brand_description", ""),
            "business_type": s.get("business_type", ""),
        }
    except Exception:
        return {}


@router.get("/stats")
async def get_seo_stats(request: Request):
    """Return aggregated SEO statistics for the dashboard."""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        sb = get_supabase()
        result = sb.table("seo_keywords").select("*").eq("tenant_id", tenant_id).execute()
        keywords = result.data or []

        positions = [kw["current_position"] for kw in keywords if kw.get("current_position")]
        total_clicks = sum((kw.get("current_clicks") or 0) for kw in keywords)
        total_impressions = sum((kw.get("current_impressions") or 0) for kw in keywords)
        avg_position = round(sum(positions) / len(positions), 1) if positions else 0
        avg_ctr = round((total_clicks / total_impressions * 100), 2) if total_impressions else 0

        return {
            "total_keywords": len(keywords),
            "avg_position": avg_position,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "avg_ctr": avg_ctr,
            "top_10": sum(1 for p in positions if p <= 10),
            "top_3": sum(1 for p in positions if p <= 3),
        }
    except Exception as e:
        return {
            "total_keywords": 0,
            "avg_position": 0,
            "total_clicks": 0,
            "total_impressions": 0,
            "avg_ctr": 0,
            "top_10": 0,
            "top_3": 0,
            "error": str(e),
        }


@router.post("/initialize")
async def initialize_keywords(request: Request):
    """Initialize keyword tracking database for the requesting tenant.

    Seeds TARGET_KEYWORDS enriched with live GSC metrics so newly inserted
    rows have real clicks/impressions/position data instead of zeros.
    """
    try:
        from shared.tenant_agents import get_seo_agent
        tenant_id = getattr(request.state, "tenant_id", "default")
        agent = await get_seo_agent(tenant_id)
        result = await agent.initialize_keywords()
        return {
            "success": True,
            "tenant_id": tenant_id,
            "message": f"Initialized {result.get('inserted', 0)} keywords ({result.get('skipped', 0)} already existed)",
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit")
async def run_audit(background_tasks: BackgroundTasks):
    """Run weekly SEO audit"""
    try:
        background_tasks.add_task(seo_agent.run_weekly_audit)
        return {
            "success": True,
            "message": "SEO audit started in background"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit/sync")
async def run_audit_sync():
    """Run SEO audit synchronously (for testing)"""
    try:
        results = await seo_agent.run_weekly_audit()
        return {"success": True, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/keywords/track")
async def track_keywords():
    """Track all keyword rankings"""
    try:
        results = await seo_agent.track_keyword_rankings()
        return {"success": True, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/keywords")
async def get_keywords(request: Request, limit: int = 1000, offset: int = 0):
    """Get all tracked keywords for the tenant."""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        sb = get_supabase()
        result = (
            sb.table("seo_keywords")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("current_impressions", desc=True)
            .range(offset, offset + min(limit, 5000) - 1)
            .execute()
        )
        keywords = result.data or []

        return {
            "total": len(keywords),
            "tenant_id": tenant_id,
            "keywords": [
                {
                    "id": kw.get("id"),
                    "keyword": kw.get("keyword") or "",
                    "intent": kw.get("intent") or "",
                    "priority": kw.get("priority") or "",
                    "target_page": kw.get("target_page") or "",
                    "current_position": kw.get("current_position") or 0,
                    "current_clicks": kw.get("current_clicks") or 0,
                    "current_impressions": kw.get("current_impressions") or 0,
                    "current_ctr": kw.get("current_ctr") or 0.0,
                    "position": kw.get("current_position") or 0,
                    "clicks": kw.get("current_clicks") or 0,
                    "impressions": kw.get("current_impressions") or 0,
                    "ctr": kw.get("current_ctr") or 0.0,
                    "position_change": kw.get("position_change") or 0,
                    "position_trend": kw.get("position_trend") or "stable",
                    "last_checked_at": kw.get("last_checked_at"),
                    "added_at": kw.get("added_at"),
                    "auto_discovered": kw.get("auto_discovered") or False,
                }
                for kw in keywords
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/keywords/top-performers")
async def get_top_performers(request: Request):
    """Get top performing keywords (position <= 10)"""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        sb = get_supabase()
        result = (
            sb.table("seo_keywords")
            .select("*")
            .eq("tenant_id", tenant_id)
            .lte("current_position", 10)
            .execute()
        )
        keywords = result.data or []

        return {
            "count": len(keywords),
            "keywords": [
                {
                    "keyword": kw.get("keyword", ""),
                    "position": kw.get("current_position", 0),
                    "clicks": kw.get("current_clicks", 0),
                    "impressions": kw.get("current_impressions", 0)
                }
                for kw in keywords
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/keywords/add")
async def add_keyword(request: Request, data: dict):
    """Add a single keyword to tracking"""
    from shared.database import get_supabase
    from datetime import datetime
    tenant_id = getattr(request.state, "tenant_id", "default")

    keyword = (data.get("keyword") or "").strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    try:
        sb = get_supabase()

        existing = (
            sb.table("seo_keywords")
            .select("id")
            .eq("keyword", keyword)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        if existing.data:
            return {"success": False, "message": f'"{keyword}" is already being tracked'}

        sb.table("seo_keywords").insert({
            "keyword": keyword,
            "tenant_id": tenant_id,
            "intent": data.get("intent", "manual"),
            "priority": data.get("priority", "medium"),
            "target_page": data.get("target_page", "/"),
            "current_position": None,
            "current_clicks": 0,
            "current_impressions": 0,
            "current_ctr": 0.0,
            "position_history": []
        }).execute()

        return {"success": True, "message": f'Now tracking "{keyword}"'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/cleanup")
async def cleanup_duplicate_actions(request: Request):
    """Remove duplicate pending actions keeping only the most recent per action_id"""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("agent_actions")
            .select("id,action_id,created_at")
            .eq("agent_name", "seo")
            .eq("status", "pending")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = result.data or []

        seen = {}
        to_delete = []
        for row in rows:
            aid = row["action_id"]
            if aid in seen:
                to_delete.append(row["id"])
            else:
                seen[aid] = row["id"]

        deleted = 0
        for uuid in to_delete:
            sb.table("agent_actions").delete().eq("id", uuid).execute()
            deleted += 1

        return {"success": True, "deleted": deleted, "remaining": len(seen)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_seo_data(request: Request, include_keywords: bool = False):
    """Reset SEO data for this tenant so analysis can start from a clean slate."""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        sb = get_supabase()

        actions_result = (
            sb.table("agent_actions")
            .delete()
            .eq("agent_name", "seo")
            .eq("tenant_id", tenant_id)
            .execute()
        )

        audits_result = (
            sb.table("seo_audits")
            .delete()
            .eq("tenant_id", tenant_id)
            .execute()
        )

        strategies_result = (
            sb.table("seo_strategies")
            .delete()
            .eq("tenant_id", tenant_id)
            .execute()
        )

        reset_summary = {
            "actions_deleted": len(actions_result.data or []),
            "audits_deleted": len(audits_result.data or []),
            "strategies_deleted": len(strategies_result.data or []),
            "keywords_deleted": 0,
            "include_keywords": include_keywords,
        }

        if include_keywords:
            keywords_result = (
                sb.table("seo_keywords")
                .delete()
                .eq("tenant_id", tenant_id)
                .execute()
            )
            reset_summary["keywords_deleted"] = len(keywords_result.data or [])

        return {
            "success": True,
            "message": "SEO data reset completed",
            "reset": reset_summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/keywords/{keyword:path}")
async def delete_keyword(keyword: str, request: Request):
    """Remove a keyword from tracking"""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("seo_keywords").delete().eq("keyword", keyword).eq("tenant_id", tenant_id).execute()
        return {"success": True, "message": f'Removed "{keyword}"'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _build_fingerprint(keywords: list) -> str:
    """Build a hash of current keyword data to detect significant changes"""
    import hashlib, json
    ranked = sorted(
        [{"k": kw["keyword"], "p": kw.get("current_position"), "c": kw.get("current_clicks", 0)}
         for kw in keywords if kw.get("current_position")],
        key=lambda x: x["k"]
    )
    raw = json.dumps({"count": len(keywords), "ranked": ranked}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _strategy_to_tasks(strategy: dict) -> list:
    """Flatten all strategy actions into a task checklist"""
    import uuid as _uuid
    from datetime import datetime
    tasks = []

    for win in strategy.get("quick_wins", []):
        tasks.append({
            "id": str(_uuid.uuid4()),
            "title": win.get("title", ""),
            "detail": win.get("action", ""),
            "category": "quick_win",
            "impact": win.get("impact", "medium"),
            "effort": win.get("effort", "medium"),
            "timeframe": win.get("timeframe", ""),
            "done": False,
            "done_at": None
        })

    for month_key, month_label in [("month1", "Month 1"), ("month2", "Month 2"), ("month3", "Month 3")]:
        for block in strategy.get(month_key, []):
            for action in block.get("actions", []):
                tasks.append({
                    "id": str(_uuid.uuid4()),
                    "title": action,
                    "detail": block.get("focus", ""),
                    "category": month_key,
                    "impact": "medium",
                    "effort": "medium",
                    "timeframe": month_label,
                    "done": False,
                    "done_at": None
                })

    for pri in strategy.get("technical_priorities", []):
        tasks.append({
            "id": str(_uuid.uuid4()),
            "title": pri,
            "detail": "",
            "category": "technical",
            "impact": "high",
            "effort": "medium",
            "timeframe": "Ongoing",
            "done": False,
            "done_at": None
        })

    return tasks


@router.get("/strategy")
async def load_seo_strategy(request: Request):
    """Load the most recent saved strategy for this tenant"""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("seo_strategies")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        row = (result.data or [None])[0]
        if not row:
            return {"success": True, "strategy": None}
        return {
            "success": True,
            "strategy": row["strategy_json"],
            "tasks": row["tasks"],
            "headline": row["headline"],
            "created_at": row["created_at"],
            "id": row["id"],
            "data_fingerprint": row["data_fingerprint"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/strategy/tasks/{task_id}")
async def update_task(task_id: str, data: dict, request: Request):
    """Toggle a task done/undone"""
    from shared.database import get_supabase
    from datetime import datetime
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("seo_strategies")
            .select("id,tasks")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        row = (result.data or [None])[0]
        if not row:
            raise HTTPException(status_code=404, detail="No strategy found")

        tasks = row["tasks"] or []
        done = data.get("done", False)
        for task in tasks:
            if task["id"] == task_id:
                task["done"] = done
                task["done_at"] = datetime.utcnow().isoformat() if done else None
                break

        sb.table("seo_strategies").update({"tasks": tasks, "updated_at": datetime.utcnow().isoformat()}).eq("id", row["id"]).execute()
        return {"success": True, "tasks": tasks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/strategy")
async def get_seo_strategy(request: Request, force: bool = False):
    """Generate a strategic SEO plan using Claude. Scoped to the requesting tenant."""
    from shared.database import get_supabase
    from anthropic import Anthropic
    from shared.config import settings
    import json, re
    from datetime import datetime
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        sb = get_supabase()

        # Load tenant brand context for a personalised AI prompt
        brand = await _get_tenant_brand(sb, tenant_id)
        brand_name = brand.get("brand_name") or tenant_id
        domain = brand.get("domain") or ""
        brand_description = brand.get("brand_description") or ""

        kw_result = sb.table("seo_keywords").select("*").eq("tenant_id", tenant_id).execute()
        keywords = kw_result.data or []

        current_fingerprint = _build_fingerprint(keywords)

        if not force:
            existing = (
                sb.table("seo_strategies")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            existing_row = (existing.data or [None])[0]
            if existing_row and existing_row.get("data_fingerprint") == current_fingerprint:
                return {
                    "success": True,
                    "cached": True,
                    "strategy": existing_row["strategy_json"],
                    "tasks": existing_row["tasks"],
                    "headline": existing_row["headline"],
                    "created_at": existing_row["created_at"],
                    "id": existing_row["id"],
                    "data_fingerprint": current_fingerprint,
                    "message": "Data hasn't changed significantly — showing existing strategy."
                }

        ranked   = [k for k in keywords if k.get("current_position") and k["current_position"] > 0]
        unranked = [k for k in keywords if not k.get("current_position")]
        top3     = [k for k in ranked if k["current_position"] <= 3]
        top10    = [k for k in ranked if k["current_position"] <= 10]
        page2    = [k for k in ranked if 11 <= k["current_position"] <= 20]

        audit_result = (
            sb.table("seo_audits")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("audit_date", desc=True)
            .limit(1)
            .execute()
        )
        latest_audit = (audit_result.data or [None])[0]

        audit_summary = ""
        if latest_audit:
            audit_summary = f"""
Latest Audit ({latest_audit.get('audit_date', '')[:10]}):
- Critical issues: {len(latest_audit.get('critical_issues') or [])}
- High issues: {len(latest_audit.get('high_issues') or [])}
- LCP: {latest_audit.get('lcp_score', 'N/A')}ms, CLS: {latest_audit.get('cls_score', 'N/A')}
"""

        kw_summary = "\n".join([
            f"- '{k['keyword']}' pos={k['current_position']} clicks={k.get('current_clicks',0)} impressions={k.get('current_impressions',0)} intent={k.get('intent','')} priority={k.get('priority','')}"
            for k in sorted(ranked, key=lambda x: x["current_position"])
        ]) or "No ranked keywords yet"

        unranked_summary = ", ".join([f"'{k['keyword']}'" for k in unranked[:10]])

        site_context = domain or brand_name
        desc_line = f" ({brand_description})" if brand_description else ""

        prompt = f"""You are an expert SEO strategist. Analyze this data for {site_context}{desc_line} and give a concrete 90-day SEO strategy.

KEYWORD DATA:
Tracked: {len(keywords)} keywords total
Ranked (have position data): {len(ranked)}
Top 3: {len(top3)} | Top 10: {len(top10)} | Page 2 (positions 11-20): {len(page2)}

Ranked keywords:
{kw_summary}

Unranked keywords (no GSC data yet): {unranked_summary}
{audit_summary}

Give your response in this exact JSON structure:
{{
  "headline": "one-line strategic summary",
  "quick_wins": [
    {{"title": "...", "action": "...", "impact": "high|medium", "effort": "low|medium|high", "timeframe": "1-2 weeks"}}
  ],
  "month1": [{{"focus": "...", "actions": ["...","...","..."]}}],
  "month2": [{{"focus": "...", "actions": ["...","...","..."]}}],
  "month3": [{{"focus": "...", "actions": ["...","...","..."]}}],
  "content_gaps": ["keyword or topic to target"],
  "technical_priorities": ["...","..."],
  "kpi_targets": {{"top10_keywords": 0, "monthly_clicks": 0, "avg_position": 0}}
}}

Be specific to {site_context}. Focus on realistic wins. Make each action a concrete single task someone can do and check off."""

        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        fallback = {"headline": text[:200], "quick_wins": [], "month1": [], "month2": [], "month3": [], "content_gaps": [], "technical_priorities": [], "kpi_targets": {}}
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                strategy = json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                strategy = fallback
        else:
            strategy = fallback

        tasks = _strategy_to_tasks(strategy)

        row = sb.table("seo_strategies").insert({
            "tenant_id": tenant_id,
            "headline": strategy.get("headline", ""),
            "strategy_json": strategy,
            "tasks": tasks,
            "data_fingerprint": current_fingerprint,
            "ranked_keywords_count": len(ranked),
            "total_keywords_count": len(keywords),
        }).execute()

        saved_id = (row.data or [{}])[0].get("id")

        return {
            "success": True,
            "cached": False,
            "strategy": strategy,
            "tasks": tasks,
            "headline": strategy.get("headline", ""),
            "created_at": datetime.utcnow().isoformat(),
            "id": saved_id,
            "data_fingerprint": current_fingerprint
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class KeywordSuggestRequest(BaseModel):
    brand_name: str = ""
    domain: str = ""
    target_audience: str = ""
    competitors: List[str] = []


@router.post("/suggest-keywords")
async def suggest_keywords(payload: KeywordSuggestRequest, request: Request):
    """Use AI to suggest relevant keywords based on brand context."""
    from shared.database import get_supabase
    from shared.config import settings
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        import anthropic
        import json

        if not payload.brand_name:
            try:
                sb = get_supabase()
                data = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
                s = data.data.get("settings", {}) if data.data else {}
                payload.brand_name = payload.brand_name or s.get("brand_name", "")
                payload.domain = payload.domain or s.get("domain", "")
                payload.target_audience = payload.target_audience or s.get("target_audience", "")
                payload.competitors = payload.competitors or s.get("competitors", [])
            except Exception:
                pass

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = f"""You are an SEO expert. Suggest 10-15 high-value keywords for the following business:

Brand: {payload.brand_name}
Website: {payload.domain}
Target audience: {payload.target_audience}
Competitors: {', '.join(payload.competitors) if payload.competitors else 'N/A'}

Return ONLY a JSON array of keyword strings, no markdown, no code fences. Example:
["keyword 1", "keyword 2", "keyword 3"]

Focus on:
- Commercial intent keywords (people ready to buy/compare)
- Informational keywords (people researching the topic)
- Long-tail keywords with lower competition
- Keywords the competitors likely target
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        try:
            keywords = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                keywords = json.loads(text.strip())
            else:
                keywords = []

        return {"keywords": keywords if isinstance(keywords, list) else []}
    except Exception as e:
        logger.error(f"suggest_keywords error: {e}")
        return {"keywords": [], "error": str(e)}


@router.post("/discover-opportunities")
async def discover_opportunities():
    """Discover new keyword opportunities"""
    try:
        opportunities = await seo_agent.discover_keyword_opportunities()
        return {
            "success": True,
            "opportunities": opportunities
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/keywords/sync-gsc")
async def sync_gsc_keywords(request: Request, min_impressions: int = 1):
    """Sync all keywords from Google Search Console into the seo_keywords table."""
    try:
        from shared.tenant_agents import get_seo_agent
        tenant_id = getattr(request.state, "tenant_id", "default")
        agent = await get_seo_agent(tenant_id)
        result = await agent.sync_gsc_keywords(min_impressions=min_impressions)
        return {"success": True, "tenant_id": tenant_id, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _gsc_queries_payload(request: Request, limit: int) -> dict:
    """Shared handler for the canonical /gsc/queries endpoint and its aliases."""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    result = (
        sb.table("seo_keywords")
        .select("keyword,current_position,current_clicks,current_impressions,current_ctr,last_checked_at,target_page")
        .eq("tenant_id", tenant_id)
        .order("current_impressions", desc=True)
        .limit(min(limit, 5000))
        .execute()
    )
    rows = result.data or []
    return {
        "tenant_id": tenant_id,
        "total": len(rows),
        "queries": [
            {
                "query": r.get("keyword") or "",
                "page": r.get("target_page") or "",
                "position": r.get("current_position") or 0,
                "clicks": r.get("current_clicks") or 0,
                "impressions": r.get("current_impressions") or 0,
                "ctr": r.get("current_ctr") or 0.0,
                "last_checked_at": r.get("last_checked_at"),
            }
            for r in rows
        ],
    }


@router.get("/gsc/queries")
@router.get("/gsc/top-queries")
@router.get("/search-console/queries")
@router.get("/keywords/gsc")
@router.get("/keywords/all")
@router.get("/rankings")
@router.get("/metrics")
async def get_gsc_queries(request: Request, limit: int = 1000):
    """Return GSC queries for the tenant."""
    try:
        return await _gsc_queries_payload(request, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actions")
async def get_seo_actions(request: Request, status: str = None, limit: int = 100):
    """Get SEO actions from database"""
    from shared.actions_db import get_actions
    tenant_id = getattr(request.state, "tenant_id", "default")
    actions = await get_actions(agent_name="seo", status=status, limit=limit, tenant_id=tenant_id)
    return {"success": True, "actions": actions}


@router.delete("/actions/{action_id}")
async def delete_action(action_id: str):
    """Delete a single action by its UUID"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        sb.table("agent_actions").delete().eq("id", action_id).execute()
        return {"success": True, "message": f"Action {action_id} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/strategy/tasks/{task_id}")
async def delete_strategy_task(task_id: str, request: Request):
    """Delete a single task from the current strategy"""
    from shared.database import get_supabase
    from datetime import datetime
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("seo_strategies")
            .select("id,tasks")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        row = (result.data or [None])[0]
        if not row:
            raise HTTPException(status_code=404, detail="No strategy found")

        tasks = row["tasks"] or []
        new_tasks = [t for t in tasks if t["id"] != task_id]
        if len(new_tasks) == len(tasks):
            raise HTTPException(status_code=404, detail="Task not found")

        sb.table("seo_strategies").update({
            "tasks": new_tasks,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", row["id"]).execute()
        return {"success": True, "tasks": new_tasks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze")
async def run_full_analysis(background: bool = True):
    """Run full SEO analysis."""
    from api.routes.seo_analyze_ooda import run_seo_analysis_with_ooda

    if background:
        from shared.background_analysis import start_background_analysis
        return await start_background_analysis("seo", run_seo_analysis_with_ooda)

    return await run_seo_analysis_with_ooda()


@router.get("/cycle-status")
async def seo_cycle_status(cycle_id: str = None):
    """Poll analysis progress."""
    from shared.background_analysis import get_cycle_status
    return await get_cycle_status("seo", cycle_id)


@router.post("/execute")
async def execute_action(action: Dict[str, Any] = Body(...)):
    """Execute an SEO action - routes to appropriate agent"""
    from agents.content import content_agent
    from shared.database import get_supabase

    if not action:
        raise HTTPException(status_code=400, detail="No action provided")

    action_type = action.get("action_type") or action.get("type", "")
    keyword = action.get("keyword", "")
    db_row_id = action.get("id")

    def mark_done(result: dict):
        if db_row_id:
            try:
                sb = get_supabase()
                from datetime import datetime
                sb.table("agent_actions").update({
                    "status": "completed",
                    "executed_at": datetime.utcnow().isoformat(),
                    "execution_result": result
                }).eq("id", db_row_id).execute()
            except Exception:
                pass

    def mark_failed(error: str):
        if db_row_id:
            try:
                sb = get_supabase()
                from datetime import datetime
                sb.table("agent_actions").update({
                    "status": "failed",
                    "executed_at": datetime.utcnow().isoformat(),
                    "error_message": error
                }).eq("id", db_row_id).execute()
            except Exception as e:
                logger.debug(f"Failed to mark action as failed in DB: {e}")

    try:
        if action_type == "content":
            title = action.get("title", keyword)
            result = await content_agent.generate_blog_post(
                topic=title,
                target_keyword=keyword,
                word_count=2000
            )

            from shared.github_helper import create_blog_post_pr
            import re as _re

            slug = _re.sub(r'[^a-z0-9]+', '-', (result.get("slug") or keyword or title).lower()).strip('-')[:60]
            pr_result = await create_blog_post_pr(
                title=result.get("title", title),
                content=result.get("content", ""),
                slug=slug,
                excerpt=result.get("meta_description", "")[:200],
                keywords=[keyword] if keyword else [],
                meta_description=result.get("meta_description", ""),
                author="SAMA SEO Agent"
            )

            outcome = {
                "success": True,
                "action_type": "content_generated",
                "keyword": keyword,
                "result": {
                    "title": result.get("title", ""),
                    "word_count": result.get("word_count", 0),
                    "status": result.get("status", "draft"),
                    "meta_description": result.get("meta_description", ""),
                    "slug": slug,
                },
                "github": pr_result
            }
            mark_done(outcome)
            return outcome

        elif action_type == "on_page":
            description = action.get("description", "")
            target_page = action.get("target_page", "")
            if seo_agent.client:
                _on_page_prompt = f"""You are an SEO specialist.

Page: {target_page or 'unknown'}
Context: {description}
Target keyword: '{keyword}'

Reply with ONLY this exact JSON (no prose outside it):
{{
  "title_tag": "...",
  "meta_description": "...",
  "h1": "...",
  "internal_links": [
    {{"anchor": "...", "url": "..."}}
  ],
  "lsi_keywords": ["...", "...", "..."],
  "quick_wins": ["one concrete sentence per win"]
}}

Rules:
- title_tag: max 60 chars, include '{keyword}'
- meta_description: max 155 chars, compelling CTA
- 3 internal links, 3 LSI keywords, 3 quick wins"""
                response = await asyncio.to_thread(
                    seo_agent.client.messages.create,
                    model=seo_agent.model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": _on_page_prompt}]
                )
                import json as _json, re as _re
                raw = response.content[0].text.strip()
                match = _re.search(r'\{[\s\S]*\}', raw)
                if match:
                    try:
                        suggestions = _json.loads(match.group())
                    except Exception:
                        suggestions = {"raw": raw}
                else:
                    suggestions = {"raw": raw}

                outcome = {
                    "success": True,
                    "action_type": "on_page_suggestions",
                    "keyword": keyword,
                    "target_page": target_page,
                    "suggestions": suggestions
                }
            else:
                outcome = {
                    "success": True,
                    "action_type": "on_page_suggestions",
                    "keyword": keyword,
                    "target_page": target_page,
                    "suggestions": {
                        "title_tag": f"{keyword.title()}",
                        "meta_description": f"{keyword.title()} — improve your online presence.",
                        "h1": f"{keyword.title()}: Complete Guide",
                        "internal_links": [],
                        "lsi_keywords": [],
                        "quick_wins": []
                    }
                }
            mark_done(outcome)
            return outcome

        elif action_type == "technical":
            target_page = action.get("target_page", "")
            title = action.get("title", "")
            url_to_check = target_page or title

            if "vs/" in url_to_check and ("404" in title.lower() or "not found" in title.lower()):
                import re
                match = re.search(r'/vs/(\w+)', url_to_check)
                if match:
                    competitor = match.group(1)
                    result = await content_agent.generate_comparison_page(competitor=competitor)
                    from shared.github_helper import create_comparison_page
                    github_result = await create_comparison_page(
                        competitor=competitor,
                        content=result.get("content", "")
                    )
                    outcome = {
                        "success": True,
                        "action_type": "comparison_page_created",
                        "competitor": competitor,
                        "github": github_result,
                    }
                    mark_done(outcome)
                    return outcome

            if seo_agent.client:
                _tech_prompt = f"""Technical SEO issue:
Issue: {title}
Details: {action.get('description', '')}
Page: {target_page}

Reply with ONLY this exact JSON:
{{
  "severity": "critical|high|medium|low",
  "estimated_effort": "30min|2h|1day|1week",
  "steps": ["concrete dev step 1", "concrete dev step 2", "..."],
  "files_to_change": ["path/to/file or component name"],
  "expected_impact": "one sentence on SEO impact after fix"
}}

Be developer-ready. No generic advice."""
                response = await asyncio.to_thread(
                    seo_agent.client.messages.create,
                    model=seo_agent.model,
                    max_tokens=700,
                    messages=[{"role": "user", "content": _tech_prompt}]
                )
                import json as _json, re as _re
                raw = response.content[0].text.strip()
                match = _re.search(r'\{[\s\S]*\}', raw)
                if match:
                    try:
                        fix_plan = _json.loads(match.group())
                    except Exception:
                        fix_plan = {"raw": raw}
                else:
                    fix_plan = {"raw": raw}

                outcome = {
                    "success": True,
                    "action_type": "technical_fix_plan",
                    "title": title,
                    "target_page": target_page,
                    "fix_plan": fix_plan
                }
            else:
                outcome = {
                    "success": True,
                    "action_type": "technical_fix_plan",
                    "title": title,
                    "fix_plan": {"steps": [f"Fix required: {title}. Add to sprint backlog."], "severity": "medium", "estimated_effort": "2h"}
                }
            mark_done(outcome)
            return outcome

        else:
            err = {"success": False, "message": f"Unknown action type: '{action_type}'"}
            mark_failed(f"Unknown action type: {action_type}")
            return err

    except Exception as e:
        mark_failed(str(e))
        return {"success": False, "error": str(e)}


@router.get("/keywords/ctr-opportunities")
async def get_ctr_opportunities():
    """
    Keywords with position <= 20 but CTR < 2%.
    """
    try:
        opportunities = await seo_agent.get_ctr_opportunities()
        return {"success": True, "count": len(opportunities), "opportunities": opportunities}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pages/insights")
async def get_page_gsc_insights():
    """
    Per-page GSC data: which pages get the most clicks/impressions.
    """
    from datetime import datetime, timedelta

    try:
        rows = await seo_agent._fetch_gsc_paginated(["page"])
        if not rows:
            return {"success": True, "pages": [], "message": "No page data available"}

        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=28)).strftime("%Y-%m-%d")

        pages = [
            {
                "page": row["keys"][0],
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": round(row.get("ctr", 0) * 100, 2),
                "avg_position": round(row.get("position", 0), 1)
            }
            for row in rows
        ]
        pages.sort(key=lambda x: x["clicks"], reverse=True)
        return {"success": True, "date_range": f"{start_date} to {end_date}", "pages": pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audits")
async def get_audit_history(request: Request, limit: int = 5):
    """Get past audit results for this tenant"""
    from shared.database import get_supabase
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("seo_audits")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("audit_date", desc=True)
            .limit(limit)
            .execute()
        )
        return {"audits": result.data or []}
    except Exception as e:
        return {"audits": [], "error": str(e)}


@router.get("/vitals")
async def get_core_web_vitals():
    """Get current Core Web Vitals"""
    try:
        vitals = await seo_agent._check_core_web_vitals()
        return {"success": True, "vitals": vitals}
    except Exception as e:
        return {"success": False, "vitals": None, "error": str(e)}
