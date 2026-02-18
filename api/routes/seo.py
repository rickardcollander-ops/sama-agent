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


@router.post("/analyze")
async def run_full_analysis():
    """Run full SEO analysis and return actionable items"""
    from shared.database import get_supabase
    
    actions = []
    vitals = None
    keyword_gaps = []
    technical_issues = []
    gsc_summary = None
    ranking_changes = None
    
    # 0. Fetch LIVE data from Google Search Console first
    try:
        gsc_summary = await seo_agent._fetch_gsc_data()
    except Exception as e:
        gsc_summary = {"status": "error", "message": str(e)}
    
    # 0b. Update keyword rankings from live GSC data
    try:
        ranking_changes = await seo_agent.track_keyword_rankings()
    except Exception as e:
        ranking_changes = {"error": str(e)}
    
    # 0c. Discover new keyword opportunities from GSC
    new_opportunities = []
    try:
        new_opportunities = await seo_agent.discover_keyword_opportunities()
        for opp in new_opportunities[:5]:
            actions.append({
                "id": f"discover-{opp['keyword'][:20]}",
                "type": "content",
                "priority": "medium",
                "title": f"New keyword opportunity: '{opp['keyword']}'",
                "description": f"Found in GSC: {opp['impressions']} impressions, position {opp.get('position', 'N/A')}, {opp['clicks']} clicks. Not yet tracked.",
                "action": f"Add '{opp['keyword']}' to tracked keywords and create targeted content",
                "keyword": opp["keyword"],
                "status": "pending"
            })
    except Exception:
        pass
    
    # 0d. Flag ranking changes from live data
    if ranking_changes and not ranking_changes.get("error"):
        for declined in ranking_changes.get("declined", []):
            if declined.get("change", 0) >= 3:
                actions.append({
                    "id": f"decline-{declined['keyword'][:20]}",
                    "type": "on_page",
                    "priority": "high",
                    "title": f"Ranking dropped: '{declined['keyword']}' #{declined['from']} → #{declined['to']}",
                    "description": f"Lost {declined['change']} positions. Investigate and optimize.",
                    "action": f"Review and strengthen content for '{declined['keyword']}' — add internal links, update content, improve meta tags",
                    "keyword": declined["keyword"],
                    "status": "pending"
                })
        for lost in ranking_changes.get("lost_top_10", []):
            actions.append({
                "id": f"lost-top10-{lost[:20]}",
                "type": "on_page",
                "priority": "critical",
                "title": f"Lost top 10: '{lost}'",
                "description": "This keyword dropped out of page 1. Immediate action needed.",
                "action": f"Urgent: optimize page for '{lost}', build internal links, consider content refresh",
                "keyword": lost,
                "status": "pending"
            })
    
    # 1. Core Web Vitals
    try:
        vitals = await seo_agent._check_core_web_vitals()
        if vitals.get("performance_score", 100) < 80:
            actions.append({
                "id": "cwv-perf",
                "type": "technical",
                "priority": "high",
                "title": f"Improve Performance Score ({vitals.get('performance_score', 0)}/100)",
                "description": f"LCP: {vitals.get('lcp', 0)}ms, CLS: {vitals.get('cls', 0)}, FCP: {vitals.get('fcp', 0)}ms",
                "action": "Optimize images, reduce JS bundle, implement lazy loading",
                "status": "pending"
            })
        if vitals.get("lcp", 0) > 2500:
            actions.append({
                "id": "cwv-lcp",
                "type": "technical",
                "priority": "critical",
                "title": f"Fix Slow LCP ({vitals.get('lcp', 0)}ms)",
                "description": "Largest Contentful Paint exceeds 2500ms threshold",
                "action": "Preload hero image, optimize server response time, use CDN",
                "status": "pending"
            })
    except Exception as e:
        vitals = {"error": str(e)}
    
    # 2. Technical SEO checks
    try:
        tech = await seo_agent._check_technical_seo()
        for issue in tech.get("critical", []):
            actions.append({
                "id": f"tech-{issue.get('type', 'unknown')}-{issue.get('url', '')[:20]}",
                "type": "technical",
                "priority": "critical",
                "title": f"{issue.get('type', '').replace('_', ' ').title()}: {issue.get('url', '')}",
                "description": issue.get("message", f"Status: {issue.get('status_code', 'N/A')}"),
                "action": "Fix immediately - affects crawling and indexing",
                "status": "pending"
            })
        for issue in tech.get("high", []):
            actions.append({
                "id": f"tech-{issue.get('type', 'unknown')}-{issue.get('url', '')[:20]}",
                "type": "on_page",
                "priority": "high",
                "title": f"{issue.get('type', '').replace('_', ' ').title()}: {issue.get('url', '')}",
                "description": "Missing SEO element that impacts rankings",
                "action": "Add missing meta tag or heading element",
                "status": "pending"
            })
        technical_issues = tech
    except Exception as e:
        technical_issues = {"error": str(e)}
    
    # 3. Keyword analysis - find content gaps
    try:
        sb = get_supabase()
        result = sb.table("seo_keywords").select("*").execute()
        keywords = result.data or []
        
        for kw in keywords:
            pos = kw.get("current_position", 0)
            keyword = kw.get("keyword", "")
            impressions = kw.get("current_impressions", 0)
            clicks = kw.get("current_clicks", 0)
            target_page = kw.get("target_page", "")
            
            # High impressions but low position = content opportunity
            if impressions > 50 and pos > 10:
                actions.append({
                    "id": f"content-{keyword[:20]}",
                    "type": "content",
                    "priority": "high",
                    "title": f"Create/optimize content for '{keyword}'",
                    "description": f"Position {pos}, {impressions} impressions but only {clicks} clicks",
                    "action": f"Generate SEO-optimized blog post targeting '{keyword}'",
                    "keyword": keyword,
                    "target_page": target_page,
                    "status": "pending"
                })
                keyword_gaps.append({"keyword": keyword, "position": pos, "impressions": impressions, "clicks": clicks})
            
            # Position 4-10 = quick win, push to top 3
            elif 4 <= pos <= 10 and impressions > 20:
                actions.append({
                    "id": f"optimize-{keyword[:20]}",
                    "type": "on_page",
                    "priority": "medium",
                    "title": f"Push '{keyword}' from #{pos} to top 3",
                    "description": f"{impressions} impressions, {clicks} clicks - close to top 3",
                    "action": f"Optimize meta title, add internal links, expand content for '{keyword}'",
                    "keyword": keyword,
                    "target_page": target_page,
                    "status": "pending"
                })
            
            # No target page = needs content
            elif not target_page and impressions > 10:
                actions.append({
                    "id": f"newpage-{keyword[:20]}",
                    "type": "content",
                    "priority": "medium",
                    "title": f"Create dedicated page for '{keyword}'",
                    "description": f"No target page assigned. {impressions} impressions suggest demand.",
                    "action": f"Generate new landing page or blog post for '{keyword}'",
                    "keyword": keyword,
                    "status": "pending"
                })
    except Exception as e:
        keyword_gaps = [{"error": str(e)}]
    
    # Sort actions by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
    
    return {
        "success": True,
        "summary": {
            "total_actions": len(actions),
            "critical": sum(1 for a in actions if a["priority"] == "critical"),
            "high": sum(1 for a in actions if a["priority"] == "high"),
            "medium": sum(1 for a in actions if a["priority"] == "medium"),
        },
        "gsc_live_data": gsc_summary,
        "ranking_changes": ranking_changes,
        "new_opportunities": new_opportunities[:5],
        "core_web_vitals": vitals,
        "technical_issues": technical_issues,
        "keyword_gaps": keyword_gaps,
        "actions": actions
    }


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
