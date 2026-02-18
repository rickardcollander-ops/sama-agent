from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from agents.reviews import review_agent

router = APIRouter()


class ReviewResponseRequest(BaseModel):
    review: Dict[str, Any]
    platform: str


class ReviewRequestRequest(BaseModel):
    customer: Dict[str, Any]
    trigger: str
    platform: str = "g2"


class ReviewAnalysisRequest(BaseModel):
    reviews: List[Dict[str, Any]]
    platform: Optional[str] = None


class CompetitorAnalysisRequest(BaseModel):
    competitor: str
    platform: str = "g2"


class OpportunityRequest(BaseModel):
    customers: List[Dict[str, Any]]


@router.get("/recent")
async def get_recent_reviews(limit: int = 20):
    """Get recent reviews from Supabase"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("reviews").select("*").order("created_at", desc=True).limit(limit).execute()
        return {"reviews": result.data or []}
    except Exception as e:
        return {"reviews": [], "error": str(e)}


@router.get("/status")
async def get_status():
    """Get Review agent status"""
    return {
        "agent": "reviews",
        "status": "operational",
        "platforms": list(review_agent.PLATFORMS.keys()),
        "triggers": list(review_agent.REVIEW_REQUEST_TRIGGERS.keys())
    }


@router.get("/platforms")
async def get_platforms():
    """Get all review platforms"""
    return {
        "platforms": review_agent.PLATFORMS
    }


@router.post("/response/generate")
async def generate_review_response(request: ReviewResponseRequest):
    """Generate response to a review"""
    try:
        result = await review_agent.generate_review_response(
            review=request.review,
            platform=request.platform
        )
        return {"success": True, "response": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/request/generate")
async def generate_review_request(request: ReviewRequestRequest):
    """Generate review request email"""
    try:
        result = await review_agent.generate_review_request(
            customer=request.customer,
            trigger=request.trigger,
            platform=request.platform
        )
        return {"success": True, "request": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze-manual")
async def analyze_reviews_manual(request: ReviewAnalysisRequest):
    """Analyze provided reviews and extract insights"""
    try:
        analysis = await review_agent.analyze_reviews(
            reviews=request.reviews,
            platform=request.platform
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actions")
async def get_reviews_actions(status: str = None, limit: int = 100):
    """Get Reviews actions from database"""
    from shared.actions_db import get_actions
    actions = await get_actions(agent_name="reviews", status=status, limit=limit)
    return {"success": True, "actions": actions}


@router.post("/analyze")
async def run_review_analysis():
    """Analyze reviews using OODA loop (Observe → Orient → Decide → Act → Reflect)"""
    from api.routes.reviews_analyze_ooda import run_reviews_analysis_with_ooda
    return await run_reviews_analysis_with_ooda()


@router.post("/analyze-legacy")
async def run_review_analysis_legacy():
    """Legacy reviews analysis (deprecated - use /analyze)"""
    from shared.database import get_supabase
    
    actions = []
    reviews = []
    
    # 1. Fetch all reviews from Supabase
    try:
        sb = get_supabase()
        result = sb.table("reviews").select("*").order("created_at", desc=True).limit(100).execute()
        reviews = result.data or []
    except Exception:
        reviews = []
    
    # 2. Find reviews needing responses
    for review in reviews:
        responded = review.get("responded", False)
        rating = review.get("rating", 5)
        author = review.get("author", "Unknown")
        platform = review.get("platform", "unknown")
        title = review.get("title", "")
        content = review.get("content", "")
        review_id = str(review.get("id", ""))
        
        if not responded:
            priority = "critical" if rating <= 2 else "high" if rating == 3 else "medium"
            actions.append({
                "id": f"review-respond-{review_id[:20]}",
                "type": "respond",
                "priority": priority,
                "title": f"Respond to {rating}-star review by {author} on {platform}",
                "description": f"{title}: {content[:150]}...",
                "action": f"Generate and post a {'empathetic, solution-oriented' if rating <= 3 else 'grateful'} response",
                "review": {
                    "id": review_id,
                    "text": content,
                    "rating": rating,
                    "reviewer": author,
                    "platform": platform,
                    "title": title
                },
                "status": "pending"
            })
    
    # 3. Check platform coverage and suggest review requests
    platform_counts = {}
    for review in reviews:
        p = review.get("platform", "unknown")
        platform_counts[p] = platform_counts.get(p, 0) + 1
    
    for platform_key, platform_info in review_agent.PLATFORMS.items():
        current = platform_counts.get(platform_info["name"], platform_counts.get(platform_key, 0))
        target = platform_info.get("target_reviews", 50)
        if current < target:
            gap = target - current
            actions.append({
                "id": f"review-request-{platform_key}",
                "type": "request_reviews",
                "priority": "high" if gap > 20 else "medium",
                "title": f"Request reviews on {platform_info['name']} ({current}/{target})",
                "description": f"Need {gap} more reviews to reach target. Current: {current}, Target: {target}.",
                "action": f"Generate personalized review request emails for happy customers on {platform_info['name']}",
                "platform": platform_key,
                "status": "pending"
            })
    
    # 4. Identify negative review trends
    negative_reviews = [r for r in reviews if r.get("rating", 5) <= 2]
    if len(negative_reviews) >= 3:
        actions.append({
            "id": "review-negative-trend",
            "type": "analyze_sentiment",
            "priority": "high",
            "title": f"Negative review trend: {len(negative_reviews)} low-rated reviews",
            "description": "Multiple negative reviews detected. Analyze common themes and address root causes.",
            "action": "Run sentiment analysis on negative reviews to identify patterns",
            "reviews": [{
                "text": r.get("content", ""),
                "rating": r.get("rating", 0),
                "platform": r.get("platform", "")
            } for r in negative_reviews[:10]],
            "status": "pending"
        })
    
    # 5. Competitor analysis suggestions
    competitors = ["gainsight", "totango", "churnzero"]
    for comp in competitors:
        actions.append({
            "id": f"review-competitor-{comp}",
            "type": "competitor_analysis",
            "priority": "low",
            "title": f"Analyze {comp.title()} reviews",
            "description": f"Monitor competitor reviews to find positioning opportunities.",
            "action": f"Analyze recent {comp.title()} reviews for common complaints we can address",
            "competitor": comp,
            "status": "pending"
        })
    
    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
    
    # Stats
    avg_rating = sum(r.get("rating", 0) for r in reviews) / len(reviews) if reviews else 0
    responded_count = sum(1 for r in reviews if r.get("responded", False))
    
    return {
        "success": True,
        "summary": {
            "total_reviews": len(reviews),
            "avg_rating": round(avg_rating, 1),
            "responded": responded_count,
            "unresponded": len(reviews) - responded_count,
            "negative_reviews": len(negative_reviews),
            "total_actions": len(actions),
            "platforms": platform_counts
        },
        "reviews": [
            {
                "id": str(r.get("id", "")),
                "platform": r.get("platform", ""),
                "rating": r.get("rating", 0),
                "author": r.get("author", ""),
                "title": r.get("title", ""),
                "content": r.get("content", "")[:200],
                "responded": r.get("responded", False),
                "created_at": r.get("created_at")
            }
            for r in reviews[:20]
        ],
        "actions": actions
    }


@router.post("/execute")
async def execute_review_action(action: Dict[str, Any] = Body(...)):
    """Execute a review action"""
    if not action:
        raise HTTPException(status_code=400, detail="No action provided")
    
    action_type = action.get("type", "")
    
    try:
        if action_type == "respond":
            review_data = action.get("review", {})
            platform = review_data.get("platform", "g2")
            result = await review_agent.generate_review_response(
                review=review_data,
                platform=platform
            )
            return {
                "success": True,
                "action_type": "response_generated",
                "result": result
            }
        
        elif action_type == "request_reviews":
            platform = action.get("platform", "g2")
            result = await review_agent.generate_review_request(
                customer={"name": "Valued Customer", "company": "Your Company"},
                trigger="milestone_reached",
                platform=platform
            )
            return {
                "success": True,
                "action_type": "request_generated",
                "result": result
            }
        
        elif action_type == "analyze_sentiment":
            reviews_to_analyze = action.get("reviews", [])
            if reviews_to_analyze:
                analysis = await review_agent.analyze_reviews(
                    reviews=reviews_to_analyze
                )
                return {
                    "success": True,
                    "action_type": "sentiment_analyzed",
                    "result": analysis
                }
            return {"success": False, "message": "No reviews to analyze"}
        
        elif action_type == "competitor_analysis":
            competitor = action.get("competitor", "")
            if competitor:
                analysis = await review_agent.monitor_competitor_reviews(
                    competitor=competitor
                )
                return {
                    "success": True,
                    "action_type": "competitor_analyzed",
                    "result": analysis
                }
            return {"success": False, "message": "No competitor specified"}
        
        else:
            return {"success": False, "message": f"Unknown action type: {action_type}"}
    
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/competitor/analyze")
async def analyze_competitor(request: CompetitorAnalysisRequest):
    """Analyze competitor reviews"""
    try:
        analysis = await review_agent.monitor_competitor_reviews(
            competitor=request.competitor,
            platform=request.platform
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/opportunities/identify")
async def identify_opportunities(request: OpportunityRequest):
    """Identify customers to ask for reviews"""
    try:
        opportunities = await review_agent.identify_review_opportunities(
            customers=request.customers
        )
        return {
            "success": True,
            "opportunities": opportunities,
            "count": len(opportunities)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/triggers")
async def get_triggers():
    """Get review request triggers"""
    return {
        "triggers": review_agent.REVIEW_REQUEST_TRIGGERS
    }


@router.get("/response-strategy")
async def get_response_strategy():
    """Get response strategy by sentiment"""
    return {
        "strategy": review_agent.RESPONSE_STRATEGY
    }
