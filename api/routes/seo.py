from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional

from agents.seo import seo_agent

router = APIRouter()


@router.get("/status")
async def get_status():
    """Get SEO agent status"""
    return {
        "agent": "seo",
        "status": "operational",
        "target_keywords": len(seo_agent.TARGET_KEYWORDS),
        "competitors": seo_agent.COMPETITORS
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
    from agents.models import Keyword
    from shared.database import AsyncSessionLocal
    from sqlalchemy import select
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Keyword))
            keywords = result.scalars().all()
            
            return {
                "total": len(keywords),
                "keywords": [
                    {
                        "keyword": kw.keyword,
                        "intent": kw.intent,
                        "priority": kw.priority,
                        "current_position": kw.current_position,
                        "target_page": kw.target_page
                    }
                    for kw in keywords
                ]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/keywords/top-performers")
async def get_top_performers():
    """Get top performing keywords (position <= 10)"""
    from agents.models import Keyword
    from shared.database import AsyncSessionLocal
    from sqlalchemy import select
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Keyword).where(Keyword.current_position <= 10)
            )
            keywords = result.scalars().all()
            
            return {
                "count": len(keywords),
                "keywords": [
                    {
                        "keyword": kw.keyword,
                        "position": kw.current_position,
                        "clicks": kw.current_clicks,
                        "impressions": kw.current_impressions
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
