from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from agents.content import content_agent

router = APIRouter()


class BlogPostRequest(BaseModel):
    topic: str
    target_keyword: Optional[str] = None
    word_count: int = 2000
    pillar: Optional[str] = None


class LandingPageRequest(BaseModel):
    topic: str
    target_keyword: str
    use_case: Optional[str] = None


class ComparisonRequest(BaseModel):
    competitor: str  # gainsight, totango, churnzero


class SocialPostRequest(BaseModel):
    topic: str
    platform: str = "twitter"
    style: str = "educational"


class SEOOptimizeRequest(BaseModel):
    content_id: str
    target_keyword: str


@router.get("/status")
async def get_status():
    """Get Content agent status"""
    return {
        "agent": "content",
        "status": "operational",
        "content_pillars": list(content_agent.brand_voice.CONTENT_PILLARS.keys()),
        "auto_publish": {
            "blog_posts": content_agent.settings.AUTO_PUBLISH_BLOG_POSTS,
            "landing_pages": content_agent.settings.AUTO_PUBLISH_LANDING_PAGES,
            "social_posts": content_agent.settings.AUTO_PUBLISH_SOCIAL_POSTS
        }
    }


@router.post("/blog")
async def generate_blog_post(request: BlogPostRequest):
    """Generate a blog post"""
    try:
        result = await content_agent.generate_blog_post(
            topic=request.topic,
            target_keyword=request.target_keyword,
            word_count=request.word_count,
            pillar=request.pillar
        )
        return {"success": True, "content": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/landing-page")
async def generate_landing_page(request: LandingPageRequest):
    """Generate a landing page"""
    try:
        result = await content_agent.generate_landing_page(
            topic=request.topic,
            target_keyword=request.target_keyword,
            use_case=request.use_case
        )
        return {"success": True, "content": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/comparison")
async def generate_comparison_page(request: ComparisonRequest):
    """Generate a competitor comparison page"""
    try:
        result = await content_agent.generate_comparison_page(
            competitor=request.competitor
        )
        return {"success": True, "content": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/social")
async def generate_social_post(request: SocialPostRequest):
    """Generate a social media post"""
    try:
        result = await content_agent.generate_social_post(
            topic=request.topic,
            platform=request.platform,
            style=request.style
        )
        return {"success": True, "content": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-seo")
async def optimize_for_seo(request: SEOOptimizeRequest):
    """Optimize existing content for SEO"""
    try:
        result = await content_agent.optimize_for_seo(
            content_id=request.content_id,
            target_keyword=request.target_keyword
        )
        return {"success": True, "analysis": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/library")
async def get_content_library():
    """Get all generated content"""
    from agents.models import ContentPiece
    from shared.database import AsyncSessionLocal
    from sqlalchemy import select
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ContentPiece).order_by(ContentPiece.created_at.desc())
            )
            content_pieces = result.scalars().all()
            
            return {
                "total": len(content_pieces),
                "content": [
                    {
                        "id": str(cp.id),
                        "title": cp.title,
                        "type": cp.content_type,
                        "status": cp.status,
                        "word_count": cp.word_count,
                        "target_keyword": cp.target_keyword,
                        "created_at": cp.created_at.isoformat() if cp.created_at else None
                    }
                    for cp in content_pieces
                ]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/library/{content_id}")
async def get_content_piece(content_id: str):
    """Get specific content piece"""
    from agents.models import ContentPiece
    from shared.database import AsyncSessionLocal
    from sqlalchemy import select
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ContentPiece).where(ContentPiece.id == content_id)
            )
            content_piece = result.scalar_one_or_none()
            
            if not content_piece:
                raise HTTPException(status_code=404, detail="Content not found")
            
            return {
                "id": str(content_piece.id),
                "title": content_piece.title,
                "content": content_piece.content,
                "type": content_piece.content_type,
                "status": content_piece.status,
                "word_count": content_piece.word_count,
                "target_keyword": content_piece.target_keyword,
                "meta_title": content_piece.meta_title,
                "meta_description": content_piece.meta_description,
                "created_at": content_piece.created_at.isoformat() if content_piece.created_at else None
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brand-voice")
async def get_brand_voice():
    """Get brand voice guidelines"""
    from agents.brand_voice import brand_voice
    
    return {
        "messaging_pillars": brand_voice.MESSAGING_PILLARS,
        "proof_points": brand_voice.PROOF_POINTS,
        "tone": brand_voice.TONE,
        "target_persona": brand_voice.TARGET_PERSONA,
        "content_pillars": brand_voice.CONTENT_PILLARS
    }
