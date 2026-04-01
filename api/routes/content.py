import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from agents.content import content_agent

logger = logging.getLogger(__name__)
from agents.brand_voice import brand_voice
from api.routes.content_chat import router as chat_router

router = APIRouter()
router.include_router(chat_router)


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
                    "id": str(cp.get("id") or ""),
                    "title": cp.get("title") or "",
                    "type": cp.get("content_type") or "",
                    "status": cp.get("status") or "draft",
                    "word_count": cp.get("word_count") or 0,
                    "target_keyword": cp.get("target_keyword") or "",
                    "meta_description": cp.get("meta_description") or "",
                    "impressions_30d": cp.get("impressions_30d") or 0,
                    "clicks_30d": cp.get("clicks_30d") or 0,
                    "avg_position": cp.get("avg_position") or 0.0,
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


@router.get("/actions")
async def get_content_actions(status: str = None, limit: int = 100):
    """Get Content actions from database"""
    from shared.actions_db import get_actions
    actions = await get_actions(agent_name="content", status=status, limit=limit)
    return {"success": True, "actions": actions}


@router.post("/analyze")
async def run_content_analysis(background: bool = True):
    """Run content analysis. With background=true (default), returns immediately with cycle_id for polling."""
    from api.routes.content_analyze_ooda import run_content_analysis_with_ooda

    if background:
        from shared.background_analysis import start_background_analysis
        return await start_background_analysis("content", run_content_analysis_with_ooda)

    return await run_content_analysis_with_ooda()


@router.get("/cycle-status")
async def content_cycle_status(cycle_id: str = None):
    """Poll analysis progress."""
    from shared.background_analysis import get_cycle_status
    return await get_cycle_status("content", cycle_id)


@router.post("/analyze-legacy")
async def run_content_analysis_legacy():
    """Legacy content analysis (deprecated - use /analyze)"""
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
    except Exception as e:
        logger.debug(f"Failed to update keyword rankings from GSC: {e}")

    # 2b. Fetch SEO keywords (now with fresh GSC data)
    seo_keywords = []
    try:
        sb = get_supabase()
        kw_result = sb.table("seo_keywords").select("*").execute()
        seo_keywords = kw_result.data or []
    except Exception as e:
        logger.debug(f"Failed to fetch SEO keywords: {e}")

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
    from shared.config import settings
    competitors = [c.split('.')[0] for c in settings.SEO_COMPETITORS]
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

    # 7. Competitor content theme gap analysis
    competitor_gap_result = {}
    try:
        competitor_gap_result = await content_agent.analyze_competitor_content_gaps()
    except Exception as e:
        logger.debug(f"Failed to analyze competitor content gaps: {e}")

    existing_action_keywords = {a.get("keyword", "").lower() for a in actions if a.get("keyword")}
    for gap in competitor_gap_result.get("gaps", []):
        target_kw = gap.get("target_keyword", "")
        if target_kw.lower() in existing_action_keywords:
            continue
        if target_kw.lower() in existing_keywords:
            continue

        comp_name = gap.get("competitor", "")
        theme = gap.get("theme", "")
        impressions = gap.get("keyword_impressions", 0)
        rec_type = gap.get("recommended_type", "blog_post")

        actions.append({
            "id": f"comp-gap-{comp_name[:10]}-{theme[:20]}".lower().replace(" ", "-"),
            "type": rec_type,
            "priority": gap.get("priority", "medium"),
            "title": gap.get("title", f"Cover competitor theme: {theme}"),
            "description": gap.get("description", f"{comp_name} covers '{theme}' but we don't."),
            "action": gap.get("action", f"Generate a {rec_type} about '{theme}' targeting '{target_kw}'"),
            "keyword": target_kw,
            "competitor": comp_name.lower() if comp_name != "organic_opportunity" else None,
            "status": "pending"
        })
        existing_action_keywords.add(target_kw.lower())

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
            "competitor_coverage": competitor_gap_result.get("coverage", {}),
            "competitor_theme_gaps": competitor_gap_result.get("total_gaps", 0),
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

    action_type = action.get("action_type") or action.get("type", "")
    keyword = action.get("keyword", "")
    db_row_id = action.get("id")  # UUID from agent_actions table

    def _mark_status(status: str, result_data: dict = None, error: str = None):
        if not db_row_id:
            return
        try:
            from shared.database import get_supabase
            from datetime import datetime
            sb = get_supabase()
            update = {"status": status, "executed_at": datetime.utcnow().isoformat()}
            if result_data:
                update["execution_result"] = result_data
            if error:
                update["error_message"] = error
            sb.table("agent_actions").update(update).eq("id", db_row_id).execute()
        except Exception as e:
            logger.debug(f"Failed to update action status in DB: {e}")

    try:
        if action_type == "blog_post":
            # Generate blog post
            result = await content_agent.generate_blog_post(
                topic=action.get("title", keyword),
                target_keyword=keyword,
                word_count=2000
            )

            # Create as PR in GitHub repo (branch → commit → PR for review)
            from shared.github_helper import create_blog_post_pr
            import re

            slug = re.sub(r'[^a-z0-9]+', '-', result.get("title", keyword).lower()).strip('-')

            github_result = await create_blog_post_pr(
                title=result.get("title", ""),
                content=result.get("content", ""),
                slug=slug,
                excerpt=result.get("meta_description", "")[:160],
                keywords=[keyword] if keyword else [],
                meta_description=result.get("meta_description", ""),
                author="SAMA Content Agent"
            )

            outcome = {
                "success": True,
                "action_type": "blog_generated",
                "result": {
                    "title": result.get("title", ""),
                    "word_count": result.get("word_count", 0),
                    "status": result.get("status", "draft"),
                    "meta_description": result.get("meta_description", ""),
                    "github": github_result
                }
            }
            _mark_status("completed", outcome)

            # Publish content_published event for social promotion
            if github_result.get("success"):
                try:
                    from shared.event_bus_registry import get_event_bus
                    bus = get_event_bus()
                    if bus:
                        await bus.publish("content_published", "sama_social", {
                            "title": result.get("title", ""),
                            "url": f"https://successifier.com/blog/{slug}",
                            "type": "blog_post",
                            "keyword": keyword,
                            "pr_url": github_result.get("pr_url", ""),
                        })
                except Exception as e:
                    logger.debug(f"Failed to publish content_published event for blog: {e}")

            return outcome

        elif action_type == "comparison":
            competitor = action.get("competitor", "")
            if competitor:
                result = await content_agent.generate_comparison_page(competitor=competitor)

                # Create comparison page as PR in GitHub
                from shared.github_helper import create_comparison_page_pr

                github_result = await create_comparison_page_pr(
                    competitor=competitor,
                    content=result.get("content", "")
                )

                outcome = {
                    "success": True,
                    "action_type": "comparison_generated",
                    "github": github_result,
                    "result": {
                        "title": result.get("title", ""),
                        "target_url": result.get("target_url", ""),
                        "status": result.get("status", "draft")
                    }
                }
                _mark_status("completed", outcome)

                # Publish content_published event for social promotion
                if github_result.get("success"):
                    try:
                        from shared.event_bus_registry import get_event_bus
                        bus = get_event_bus()
                        if bus:
                            await bus.publish("content_published", "sama_social", {
                                "title": result.get("title", f"Successifier vs {competitor.title()}"),
                                "url": f"https://successifier.com/vs/{competitor.lower().replace(' ', '-')}",
                                "type": "comparison",
                                "competitor": competitor,
                                "pr_url": github_result.get("pr_url", ""),
                            })
                    except Exception as e:
                        logger.debug(f"Failed to publish content_published event for comparison: {e}")

                return outcome
            return {"success": False, "message": "No competitor specified"}
        
        elif action_type == "optimize":
            content_id = action.get("content_id", "")
            if content_id and keyword:
                result = await content_agent.optimize_for_seo(
                    content_id=content_id,
                    target_keyword=keyword
                )
                outcome = {
                    "success": True,
                    "action_type": "content_optimized",
                    "result": result
                }
                _mark_status("completed", outcome)
                return outcome
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
                    outcome = {
                        "success": True,
                        "action_type": "meta_generated",
                        "meta_description": meta
                    }
                    _mark_status("completed", outcome)
                    return outcome
            return {"success": False, "message": "Content not found"}
        
        elif action_type == "publish":
            content_id = action.get("content_id", "")
            if content_id:
                from shared.database import get_supabase
                sb = get_supabase()
                sb.table("content_pieces").update({"status": "published"}).eq("id", content_id).execute()
                outcome = {
                    "success": True,
                    "action_type": "content_published",
                    "content_id": content_id
                }
                _mark_status("completed", outcome)
                return outcome
            return {"success": False, "message": "No content_id specified"}

        else:
            _mark_status("failed", error=f"Unknown action type: {action_type}")
            return {"success": False, "message": f"Unknown action type: {action_type}"}

    except Exception as e:
        _mark_status("failed", error=str(e))
        return {"success": False, "error": str(e)}
