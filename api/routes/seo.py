from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from agents.seo import seo_agent
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


@router.post("/initialize")
async def initialize_keywords():
    """Initialize keyword tracking database"""
    try:
        await seo_agent.initialize_keywords()
        return {
            "success": True,
            "message": f"Initialized {len(seo_agent.TARGET_KEYWORDS)} keywords"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit")
async def run_audit(background_tasks: BackgroundTasks):
    """Run weekly SEO audit"""
    try:
        # Run audit in background for long-running task
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
async def get_keywords():
    """Get all tracked keywords"""
    from shared.database import get_supabase
    
    try:
        sb = get_supabase()
        result = sb.table("seo_keywords").select("*").limit(100).execute()
        keywords = result.data or []
        
        return {
            "total": len(keywords),
            "keywords": [
                {
                    "keyword": kw.get("keyword", ""),
                    "intent": kw.get("intent", ""),
                    "priority": kw.get("priority", ""),
                    "current_position": kw.get("current_position", 0),
                    "current_clicks": kw.get("current_clicks", 0),
                    "current_impressions": kw.get("current_impressions", 0),
                    "target_page": kw.get("target_page", "")
                }
                for kw in keywords
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/keywords/top-performers")
async def get_top_performers():
    """Get top performing keywords (position <= 10)"""
    from shared.database import get_supabase
    
    try:
        sb = get_supabase()
        result = sb.table("seo_keywords").select("*").lte("current_position", 10).execute()
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
async def add_keyword(data: dict):
    """Add a single keyword to tracking"""
    from shared.database import get_supabase
    from datetime import datetime

    keyword = (data.get("keyword") or "").strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    try:
        sb = get_supabase()

        # Check for duplicate
        existing = sb.table("seo_keywords").select("id").eq("keyword", keyword).execute()
        if existing.data:
            return {"success": False, "message": f'"{keyword}" is already being tracked'}

        sb.table("seo_keywords").insert({
            "keyword": keyword,
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
async def cleanup_duplicate_actions():
    """Remove duplicate pending actions keeping only the most recent per action_id"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        # Get all pending SEO actions
        result = sb.table("agent_actions").select("id,action_id,created_at").eq("agent_name", "seo").eq("status", "pending").order("created_at", desc=True).execute()
        rows = result.data or []

        # Group by action_id, keep newest, collect rest for deletion
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


@router.delete("/keywords/{keyword:path}")
async def delete_keyword(keyword: str):
    """Remove a keyword from tracking"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("seo_keywords").delete().eq("keyword", keyword).execute()
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
async def load_seo_strategy():
    """Load the most recent saved strategy"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("seo_strategies").select("*").order("created_at", desc=True).limit(1).execute()
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
async def update_task(task_id: str, data: dict):
    """Toggle a task done/undone"""
    from shared.database import get_supabase
    from datetime import datetime
    import json
    try:
        sb = get_supabase()
        result = sb.table("seo_strategies").select("id,tasks").order("created_at", desc=True).limit(1).execute()
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
async def get_seo_strategy(force: bool = False):
    """Generate a strategic SEO plan using Claude. Skips generation if data hasn't changed significantly."""
    from shared.database import get_supabase
    from anthropic import Anthropic
    from shared.config import settings
    import json, re
    from datetime import datetime

    try:
        sb = get_supabase()

        # Gather keyword data
        kw_result = sb.table("seo_keywords").select("*").execute()
        keywords = kw_result.data or []

        current_fingerprint = _build_fingerprint(keywords)

        # Check if existing strategy has same fingerprint
        if not force:
            existing = sb.table("seo_strategies").select("*").order("created_at", desc=True).limit(1).execute()
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

        ranked    = [k for k in keywords if k.get("current_position") and k["current_position"] > 0]
        unranked  = [k for k in keywords if not k.get("current_position")]
        top3      = [k for k in ranked if k["current_position"] <= 3]
        top10     = [k for k in ranked if k["current_position"] <= 10]
        page2     = [k for k in ranked if 11 <= k["current_position"] <= 20]

        # Latest audit
        audit_result = sb.table("seo_audits").select("*").order("audit_date", desc=True).limit(1).execute()
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

        prompt = f"""You are an expert SEO strategist. Analyze this data for successifier.com (AI customer success platform) and give a concrete 90-day SEO strategy.

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

Be specific to successifier.com and the customer success SaaS space. Focus on realistic wins. Make each action in month1/month2/month3 a concrete single task someone can do and check off."""

        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            strategy = json.loads(match.group())
        else:
            strategy = {"headline": text, "quick_wins": [], "month1": [], "month2": [], "month3": [], "content_gaps": [], "technical_priorities": [], "kpi_targets": {}}

        tasks = _strategy_to_tasks(strategy)

        # Save to DB
        row = sb.table("seo_strategies").insert({
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


@router.get("/actions")
async def get_seo_actions(status: str = None, limit: int = 100):
    """Get SEO actions from database"""
    from shared.actions_db import get_actions
    actions = await get_actions(agent_name="seo", status=status, limit=limit)
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
async def delete_strategy_task(task_id: str):
    """Delete a single task from the current strategy"""
    from shared.database import get_supabase
    from datetime import datetime
    try:
        sb = get_supabase()
        result = sb.table("seo_strategies").select("id,tasks").order("created_at", desc=True).limit(1).execute()
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
async def run_full_analysis():
    """Run full SEO analysis using OODA loop (Observe → Orient → Decide → Act → Reflect)"""
    from api.routes.seo_analyze_ooda import run_seo_analysis_with_ooda
    return await run_seo_analysis_with_ooda()


@router.post("/execute")
async def execute_action(action: Dict[str, Any] = Body(...)):
    """Execute an SEO action - routes to appropriate agent"""
    from agents.content import content_agent
    from shared.database import get_supabase

    if not action:
        raise HTTPException(status_code=400, detail="No action provided")

    # DB stores as action_type; dashboard may send either field name
    action_type = action.get("action_type") or action.get("type", "")
    keyword = action.get("keyword", "")
    db_row_id = action.get("id")  # UUID primary key in agent_actions table

    def mark_done(result: dict):
        """Update status to completed in DB"""
        if db_row_id:
            try:
                sb = get_supabase()
                from datetime import datetime
                sb.table("agent_actions").update({
                    "status": "completed",
                    "executed_at": datetime.utcnow().isoformat(),
                    "execution_result": result
                }).eq("id", db_row_id).execute()
            except Exception as e:
                pass  # Don't fail the response if DB update fails

    def mark_failed(error: str):
        """Update status to failed in DB"""
        if db_row_id:
            try:
                sb = get_supabase()
                from datetime import datetime
                sb.table("agent_actions").update({
                    "status": "failed",
                    "executed_at": datetime.utcnow().isoformat(),
                    "error_message": error
                }).eq("id", db_row_id).execute()
            except Exception:
                pass

    try:
        if action_type == "content":
            # Generate blog post via Content Agent
            title = action.get("title", keyword)
            result = await content_agent.generate_blog_post(
                topic=title,
                target_keyword=keyword,
                word_count=2000
            )
            outcome = {
                "success": True,
                "action_type": "content_generated",
                "keyword": keyword,
                "result": {
                    "title": result.get("title", ""),
                    "word_count": result.get("word_count", 0),
                    "status": result.get("status", "draft"),
                    "meta_description": result.get("meta_description", "")
                }
            }
            mark_done(outcome)
            return outcome

        elif action_type == "on_page":
            # Generate concrete meta optimisation suggestions via Claude
            description = action.get("description", "")
            if seo_agent.client:
                response = seo_agent.client.messages.create(
                    model=seo_agent.model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": f"""You are an SEO specialist for successifier.com — an AI customer success platform.

Action context: {description}
Target keyword: '{keyword}'

Provide:
1. Optimised title tag (max 60 chars)
2. Meta description (max 155 chars)
3. H1 heading
4. 3 internal links to add (use real successifier.com pages like /product, /pricing, /blog, /vs/gainsight)
5. 3 LSI keywords to weave into the content

Be specific — no generic advice."""}]
                )
                outcome = {
                    "success": True,
                    "action_type": "meta_optimization",
                    "keyword": keyword,
                    "suggestions": response.content[0].text
                }
            else:
                outcome = {
                    "success": True,
                    "action_type": "meta_optimization",
                    "keyword": keyword,
                    "suggestions": f"Title: {keyword.title()} | Successifier\nDescription: {keyword.title()} with Successifier's AI-powered CS platform.\nH1: {keyword.title()}: Complete Guide"
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
                        "url": f"https://successifier.com/vs/{competitor}"
                    }
                    mark_done(outcome)
                    return outcome

            # Other technical issues: generate a concrete fix plan via Claude
            if seo_agent.client:
                response = seo_agent.client.messages.create(
                    model=seo_agent.model,
                    max_tokens=512,
                    messages=[{"role": "user", "content": f"""Technical SEO issue on successifier.com:
Issue: {title}
Details: {action.get('description', '')}

Give a concise, developer-ready fix (3-5 steps). Be specific."""}]
                )
                outcome = {
                    "success": True,
                    "action_type": "technical_fix_plan",
                    "fix_plan": response.content[0].text
                }
            else:
                outcome = {
                    "success": True,
                    "action_type": "technical_flagged",
                    "message": f"Fix required: {title}. Add to sprint backlog."
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


@router.get("/audits")
async def get_audit_history(limit: int = 5):
    """Get past audit results"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("seo_audits").select("*").order("audit_date", desc=True).limit(limit).execute()
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
