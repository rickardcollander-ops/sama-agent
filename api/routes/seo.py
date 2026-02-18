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
