"""
Advanced Content API Routes
Pillar pages, FAQ pages, content briefs, content refresh
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import logging

from agents.content_advanced import advanced_content_generator

router = APIRouter()
logger = logging.getLogger(__name__)


class PillarPageRequest(BaseModel):
    topic: str
    target_keyword: str
    pillar: str
    subtopics: List[str]


class FAQPageRequest(BaseModel):
    topic: str
    target_keyword: str
    num_questions: int = 10


class ContentBriefRequest(BaseModel):
    keyword: str
    search_intent: str
    competitor_urls: List[str]


class ContentRefreshRequest(BaseModel):
    content_id: str


@router.post("/pillar-page")
async def generate_pillar_page(request: PillarPageRequest):
    """
    Generate comprehensive pillar page (3000-5000 words)
    
    Pillar pages serve as ultimate guides on a topic
    """
    try:
        result = await advanced_content_generator.generate_pillar_page(
            topic=request.topic,
            target_keyword=request.target_keyword,
            pillar=request.pillar,
            subtopics=request.subtopics
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to generate pillar page: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/faq-page")
async def generate_faq_page(request: FAQPageRequest):
    """
    Generate FAQ page with schema markup
    
    Includes JSON-LD FAQ schema for rich snippets
    """
    try:
        result = await advanced_content_generator.generate_faq_page(
            topic=request.topic,
            target_keyword=request.target_keyword,
            num_questions=request.num_questions
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to generate FAQ page: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content-brief")
async def generate_content_brief(request: ContentBriefRequest):
    """
    Generate detailed content brief for writers
    
    Includes:
    - Recommended word count
    - Title suggestions
    - Outline with talking points
    - Keywords to include
    - Competitor analysis
    - Unique angles
    """
    try:
        result = await advanced_content_generator.generate_content_brief(
            keyword=request.keyword,
            search_intent=request.search_intent,
            competitor_urls=request.competitor_urls
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to generate content brief: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/identify")
async def identify_content_for_refresh():
    """
    Identify content that needs refreshing (30+ days old)
    
    Returns list of content pieces that should be updated
    """
    try:
        content = await advanced_content_generator.identify_content_for_refresh()
        
        return {
            "success": True,
            "total_identified": len(content),
            "content": content
        }
        
    except Exception as e:
        logger.error(f"Failed to identify content for refresh: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh")
async def refresh_content(request: ContentRefreshRequest):
    """
    Refresh existing content with updated information
    
    Updates:
    - Statistics and data points
    - New insights and trends
    - SEO optimization
    - Examples and case studies
    """
    try:
        result = await advanced_content_generator.refresh_content(request.content_id)
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to refresh content: {e}")
        raise HTTPException(status_code=500, detail=str(e))
