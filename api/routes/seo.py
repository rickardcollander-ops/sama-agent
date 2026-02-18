from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from agents.seo import seo_agent

router = APIRouter()


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
    
    if not action:
        raise HTTPException(status_code=400, detail="No action provided")
    
    action_type = action.get("type", "")
    keyword = action.get("keyword", "")
    
    try:
        if action_type == "content":
            # Generate blog post via Content Agent
            title = action.get("title", keyword)
            result = await content_agent.generate_blog_post(
                topic=title,
                target_keyword=keyword,
                word_count=2000
            )
            return {
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
        elif action_type == "on_page":
            # Generate meta optimization suggestions
            if seo_agent.client:
                response = seo_agent.client.messages.create(
                    model=seo_agent.model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": f"""Generate optimized SEO meta tags for the keyword '{keyword}' for successifier.com (a customer success platform):

1. Title tag (max 60 chars)
2. Meta description (max 155 chars)  
3. H1 heading
4. 3 internal link suggestions
5. 3 related keywords to include in content

Be specific and actionable."""}]
                )
                return {
                    "success": True,
                    "action_type": "meta_optimization",
                    "keyword": keyword,
                    "suggestions": response.content[0].text
                }
            else:
                return {
                    "success": True,
                    "action_type": "meta_optimization",
                    "keyword": keyword,
                    "suggestions": f"Title: {keyword.title()} | Successifier - Customer Success Platform\nDescription: Learn about {keyword} with Successifier's AI-powered customer success platform.\nH1: {keyword.title()}: Complete Guide"
                }
        elif action_type == "technical":
            # Check if this is a 404 for a comparison page
            title = action.get("title", "")
            if "vs/" in title and "404" in title.lower():
                # Extract competitor name from URL
                import re
                match = re.search(r'/vs/(\w+)', title)
                if match:
                    competitor = match.group(1)
                    
                    # Generate comparison page via Content Agent
                    result = await content_agent.generate_comparison_page(competitor=competitor)
                    
                    # Push to GitHub
                    from shared.github_helper import create_comparison_page
                    github_result = await create_comparison_page(
                        competitor=competitor,
                        content=result.get("content", "")
                    )
                    
                    return {
                        "success": True,
                        "action_type": "comparison_page_created",
                        "competitor": competitor,
                        "github": github_result,
                        "url": f"https://successifier.com/vs/{competitor}"
                    }
            
            # Other technical issues just get flagged
            return {
                "success": True,
                "action_type": "technical_flagged",
                "message": "Technical issue flagged for development team. Add to sprint backlog."
            }
        else:
            return {"success": False, "message": f"Unknown action type: {action_type}"}
    except Exception as e:
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
