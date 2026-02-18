from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

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
    from shared.database import get_supabase
    
    try:
        sb = get_supabase()
        result = sb.table("content_pieces").select("*").order("created_at", desc=True).limit(50).execute()
        pieces = result.data or []
        
        return {
            "total": len(pieces),
            "content": [
                {
                    "id": str(cp.get("id", "")),
                    "title": cp.get("title", ""),
                    "type": cp.get("content_type", ""),
                    "status": cp.get("status", "draft"),
                    "word_count": cp.get("word_count", 0),
                    "target_keyword": cp.get("target_keyword", ""),
                    "meta_description": cp.get("meta_description", ""),
                    "created_at": cp.get("created_at")
                }
                for cp in pieces
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/library/{content_id}")
async def get_content_piece(content_id: str):
    """Get specific content piece"""
    from shared.database import get_supabase
    
    try:
        sb = get_supabase()
        result = sb.table("content_pieces").select("*").eq("id", content_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Content not found")
        
        cp = result.data[0]
        return {
            "id": str(cp.get("id", "")),
            "title": cp.get("title", ""),
            "content": cp.get("content", ""),
            "type": cp.get("content_type", ""),
            "status": cp.get("status", "draft"),
            "word_count": cp.get("word_count", 0),
            "target_keyword": cp.get("target_keyword", ""),
            "meta_title": cp.get("meta_title", ""),
            "meta_description": cp.get("meta_description", ""),
            "created_at": cp.get("created_at")
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


@router.post("/analyze")
async def run_content_analysis():
    """Analyze content library and generate actionable recommendations"""
    from shared.database import get_supabase
    from agents.brand_voice import brand_voice
    
    actions = []
    content_pieces = []
    
    # 1. Fetch existing content from Supabase
    try:
        sb = get_supabase()
        result = sb.table("content_pieces").select("*").order("created_at", desc=True).limit(100).execute()
        content_pieces = result.data or []
    except Exception:
        content_pieces = []
    
    # 2. Update keyword data from live GSC before analyzing gaps
    try:
        from agents.seo import seo_agent as _seo
        await _seo.track_keyword_rankings()
    except Exception:
        pass
    
    # 2b. Fetch SEO keywords (now with fresh GSC data)
    seo_keywords = []
    try:
        sb = get_supabase()
        kw_result = sb.table("seo_keywords").select("*").execute()
        seo_keywords = kw_result.data or []
    except Exception:
        pass
    
    # 3. Analyze content gaps - keywords without content
    existing_keywords = set()
    for cp in content_pieces:
        kw = cp.get("target_keyword", "")
        if kw:
            existing_keywords.add(kw.lower())
    
    for kw in seo_keywords:
        keyword = kw.get("keyword", "")
        if keyword.lower() not in existing_keywords:
            position = kw.get("current_position", 0)
            impressions = kw.get("current_impressions", 0)
            priority = "high" if impressions > 100 else "medium"
            actions.append({
                "id": f"content-gap-{keyword[:30]}",
                "type": "blog_post",
                "priority": priority,
                "title": f"Create content for: '{keyword}'",
                "description": f"No content targeting this keyword. Position: {position}, Impressions: {impressions}.",
                "action": f"Generate a blog post targeting '{keyword}' to capture organic traffic",
                "keyword": keyword,
                "status": "pending"
            })
    
    # 4. Analyze existing content for optimization
    for cp in content_pieces:
        title = cp.get("title", "")
        word_count = cp.get("word_count", 0)
        status = cp.get("status", "")
        target_kw = cp.get("target_keyword", "")
        content_type = cp.get("content_type", "")
        
        # Thin content
        if word_count > 0 and word_count < 1000 and content_type == "blog":
            actions.append({
                "id": f"content-thin-{cp.get('id', '')[:20]}",
                "type": "optimize",
                "priority": "high",
                "title": f"Expand thin content: '{title[:50]}'",
                "description": f"Only {word_count} words. Blog posts should be 1500+ words for SEO.",
                "action": "Expand content with more detail, examples, and data points",
                "content_id": cp.get("id", ""),
                "keyword": target_kw,
                "status": "pending"
            })
        
        # Missing meta description
        if not cp.get("meta_description"):
            actions.append({
                "id": f"content-meta-{cp.get('id', '')[:20]}",
                "type": "meta",
                "priority": "medium",
                "title": f"Add meta description: '{title[:50]}'",
                "description": "Missing meta description hurts CTR in search results.",
                "action": "Generate an SEO-optimized meta description (150-160 chars)",
                "content_id": cp.get("id", ""),
                "keyword": target_kw,
                "status": "pending"
            })
        
        # Draft content that should be published
        if status == "draft":
            actions.append({
                "id": f"content-publish-{cp.get('id', '')[:20]}",
                "type": "publish",
                "priority": "medium",
                "title": f"Publish draft: '{title[:50]}'",
                "description": f"Content is still in draft status. {word_count} words, type: {content_type}.",
                "action": "Review and publish this content",
                "content_id": cp.get("id", ""),
                "status": "pending"
            })
    
    # 5. Check content pillars coverage
    pillar_content = {}
    for cp in content_pieces:
        ct = cp.get("content_type", "")
        pillar_content[ct] = pillar_content.get(ct, 0) + 1
    
    for pillar_key, pillar_info in brand_voice.CONTENT_PILLARS.items():
        pillar_title = pillar_info.get("title", pillar_key)
        # Check if we have enough content for each pillar
        matching = [cp for cp in content_pieces if pillar_key.lower() in (cp.get("target_keyword", "") or "").lower() or pillar_key.lower() in (cp.get("title", "") or "").lower()]
        if len(matching) < 2:
            actions.append({
                "id": f"content-pillar-{pillar_key}",
                "type": "blog_post",
                "priority": "medium",
                "title": f"Expand pillar: {pillar_title}",
                "description": f"Only {len(matching)} pieces for '{pillar_title}' pillar. Need more topical authority.",
                "action": f"Generate blog posts for the '{pillar_title}' content pillar",
                "pillar": pillar_key,
                "status": "pending"
            })
    
    # 6. Competitor comparison pages
    competitors = ["gainsight", "totango", "churnzero"]
    existing_comparisons = [cp for cp in content_pieces if cp.get("content_type") == "comparison"]
    existing_comp_names = [cp.get("title", "").lower() for cp in existing_comparisons]
    for comp in competitors:
        if not any(comp in name for name in existing_comp_names):
            actions.append({
                "id": f"content-comparison-{comp}",
                "type": "comparison",
                "priority": "high",
                "title": f"Create comparison: Successifier vs {comp.title()}",
                "description": f"No comparison page for {comp.title()}. These pages convert well.",
                "action": f"Generate comparison page targeting '{comp} alternative'",
                "competitor": comp,
                "status": "pending"
            })
    
    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
    
    return {
        "success": True,
        "summary": {
            "total_actions": len(actions),
            "high": sum(1 for a in actions if a["priority"] == "high"),
            "medium": sum(1 for a in actions if a["priority"] == "medium"),
            "content_pieces": len(content_pieces),
            "keywords_tracked": len(seo_keywords),
            "content_gaps": sum(1 for a in actions if a["type"] == "blog_post"),
        },
        "content": [
            {
                "id": str(cp.get("id", "")),
                "title": cp.get("title", ""),
                "type": cp.get("content_type", ""),
                "status": cp.get("status", ""),
                "word_count": cp.get("word_count", 0),
                "target_keyword": cp.get("target_keyword", ""),
                "created_at": cp.get("created_at")
            }
            for cp in content_pieces[:20]
        ],
        "actions": actions
    }


@router.post("/execute")
async def execute_content_action(action: Dict[str, Any] = Body(...)):
    """Execute a content action"""
    if not action:
        raise HTTPException(status_code=400, detail="No action provided")
    
    action_type = action.get("type", "")
    keyword = action.get("keyword", "")
    
    try:
        if action_type == "blog_post":
            pillar = action.get("pillar")
            result = await content_agent.generate_blog_post(
                topic=keyword or action.get("title", ""),
                target_keyword=keyword,
                word_count=2000,
                pillar=pillar
            )
            return {
                "success": True,
                "action_type": "blog_generated",
                "result": {
                    "title": result.get("title", ""),
                    "word_count": result.get("word_count", 0),
                    "meta_description": result.get("meta_description", ""),
                    "status": result.get("status", "draft")
                }
            }
        
        elif action_type == "comparison":
            competitor = action.get("competitor", "")
            if competitor:
                result = await content_agent.generate_comparison_page(competitor=competitor)
                return {
                    "success": True,
                    "action_type": "comparison_generated",
                    "result": {
                        "title": result.get("title", ""),
                        "target_url": result.get("target_url", ""),
                        "status": result.get("status", "draft")
                    }
                }
            return {"success": False, "message": "No competitor specified"}
        
        elif action_type == "optimize":
            content_id = action.get("content_id", "")
            if content_id and keyword:
                result = await content_agent.optimize_for_seo(
                    content_id=content_id,
                    target_keyword=keyword
                )
                return {
                    "success": True,
                    "action_type": "content_optimized",
                    "result": result
                }
            return {"success": False, "message": "Missing content_id or keyword"}
        
        elif action_type == "meta":
            content_id = action.get("content_id", "")
            if content_id:
                from shared.database import get_supabase
                sb = get_supabase()
                cp_result = sb.table("content_pieces").select("title,content").eq("id", content_id).execute()
                if cp_result.data:
                    cp = cp_result.data[0]
                    meta = await content_agent._generate_meta_description(cp["title"], cp.get("content", ""))
                    sb.table("content_pieces").update({"meta_description": meta}).eq("id", content_id).execute()
                    return {
                        "success": True,
                        "action_type": "meta_generated",
                        "meta_description": meta
                    }
            return {"success": False, "message": "Content not found"}
        
        elif action_type == "publish":
            content_id = action.get("content_id", "")
            if content_id:
                from shared.database import get_supabase
                sb = get_supabase()
                sb.table("content_pieces").update({"status": "published"}).eq("id", content_id).execute()
                return {
                    "success": True,
                    "action_type": "content_published",
                    "content_id": content_id
                }
            return {"success": False, "message": "No content_id specified"}
        
        else:
            return {"success": False, "message": f"Unknown action type: {action_type}"}
    
    except Exception as e:
        return {"success": False, "error": str(e)}
