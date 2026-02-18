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
    """Execute a review action and persist result to DB"""
    from shared.database import get_supabase
    from datetime import datetime

    if not action:
        raise HTTPException(status_code=400, detail="No action provided")

    action_type = action.get("action_type") or action.get("type", "")
    db_row_id = action.get("id")

    def _mark_done(result: dict):
        if db_row_id:
            try:
                sb = get_supabase()
                sb.table("agent_actions").update({
                    "status": "completed",
                    "executed_at": datetime.utcnow().isoformat(),
                    "execution_result": result
                }).eq("id", db_row_id).execute()
            except Exception:
                pass

    def _mark_failed(error: str):
        if db_row_id:
            try:
                sb = get_supabase()
                sb.table("agent_actions").update({
                    "status": "failed",
                    "executed_at": datetime.utcnow().isoformat(),
                    "error_message": error
                }).eq("id", db_row_id).execute()
            except Exception:
                pass

    try:
        if action_type == "respond":
            review_data = action.get("review", {})
            platform = review_data.get("platform", "g2")
            result = await review_agent.generate_review_response(
                review=review_data,
                platform=platform
            )
            outcome = {
                "success": True,
                "action_type": "response_generated",
                "result": result
            }
            _mark_done(outcome)
            return outcome

        elif action_type == "request_reviews":
            platform = action.get("platform", "g2")
            result = await review_agent.generate_review_request(
                customer={"name": "Valued Customer", "company": "Your Company"},
                trigger="milestone_reached",
                platform=platform
            )
            outcome = {
                "success": True,
                "action_type": "request_generated",
                "result": result
            }
            _mark_done(outcome)
            return outcome

        elif action_type == "analyze_sentiment":
            reviews_to_analyze = action.get("reviews", [])
            if reviews_to_analyze:
                analysis = await review_agent.analyze_reviews(
                    reviews=reviews_to_analyze
                )
                outcome = {
                    "success": True,
                    "action_type": "sentiment_analyzed",
                    "result": analysis
                }
                _mark_done(outcome)
                return outcome
            outcome = {"success": False, "message": "No reviews to analyze"}
            _mark_failed("No reviews to analyze")
            return outcome

        elif action_type == "competitor_analysis":
            competitor = action.get("competitor", "")
            if competitor:
                analysis = await review_agent.monitor_competitor_reviews(
                    competitor=competitor
                )
                outcome = {
                    "success": True,
                    "action_type": "competitor_analyzed",
                    "result": analysis
                }
                _mark_done(outcome)
                return outcome
            outcome = {"success": False, "message": "No competitor specified"}
            _mark_failed("No competitor specified")
            return outcome

        else:
            outcome = {"success": False, "message": f"Unknown action type: {action_type}"}
            _mark_failed(f"Unknown action type: {action_type}")
            return outcome

    except Exception as e:
        _mark_failed(str(e))
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


@router.get("/dashboard")
async def get_dashboard_stats():
    """Get full dashboard stats: reviews, platform breakdown, sentiment, SLA"""
    from shared.database import get_supabase
    from datetime import datetime, timedelta
    try:
        sb = get_supabase()

        # All reviews
        result = sb.table("reviews").select("*").order("created_at", desc=True).limit(200).execute()
        reviews = result.data or []

        total = len(reviews)
        avg_rating = sum(r.get("rating", 0) for r in reviews) / total if total else 0
        responded_count = sum(1 for r in reviews if r.get("responded", False))
        unresponded = total - responded_count

        # Sentiment distribution
        positive = sum(1 for r in reviews if r.get("rating", 0) >= 4)
        neutral = sum(1 for r in reviews if r.get("rating", 0) == 3)
        negative = sum(1 for r in reviews if r.get("rating", 0) <= 2)

        # Platform breakdown
        platforms = {}
        for r in reviews:
            p = r.get("platform", "Unknown")
            if p not in platforms:
                platforms[p] = {"count": 0, "total_rating": 0, "responded": 0}
            platforms[p]["count"] += 1
            platforms[p]["total_rating"] += r.get("rating", 0)
            if r.get("responded"):
                platforms[p]["responded"] += 1

        platform_stats = {}
        for p, data in platforms.items():
            platform_stats[p] = {
                "count": data["count"],
                "avg_rating": round(data["total_rating"] / data["count"], 1) if data["count"] else 0,
                "responded": data["responded"],
                "unresponded": data["count"] - data["responded"],
            }

        # Rating distribution
        rating_dist = {str(i): sum(1 for r in reviews if r.get("rating") == i) for i in range(1, 6)}

        # Recent trend (last 30 days vs previous 30)
        now = datetime.utcnow()
        cutoff_30 = (now - timedelta(days=30)).isoformat()
        cutoff_60 = (now - timedelta(days=60)).isoformat()
        recent = [r for r in reviews if r.get("created_at", "") >= cutoff_30]
        previous = [r for r in reviews if cutoff_60 <= r.get("created_at", "") < cutoff_30]
        recent_avg = sum(r.get("rating", 0) for r in recent) / len(recent) if recent else 0
        previous_avg = sum(r.get("rating", 0) for r in previous) / len(previous) if previous else 0

        # SLA summary
        sla_violations = 0
        sla_warnings = 0
        for r in reviews:
            if not r.get("responded") and r.get("created_at"):
                try:
                    created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00").replace("+00:00", ""))
                    hours_elapsed = (now - created).total_seconds() / 3600
                    rating = r.get("rating", 5)
                    threshold = {5: 24, 4: 12, 3: 6, 2: 3, 1: 2}.get(rating, 24)
                    if hours_elapsed > threshold:
                        sla_violations += 1
                    elif hours_elapsed > threshold * 0.75:
                        sla_warnings += 1
                except Exception:
                    pass

        return {
            "success": True,
            "stats": {
                "total_reviews": total,
                "avg_rating": round(avg_rating, 1),
                "responded": responded_count,
                "unresponded": unresponded,
                "positive": positive,
                "neutral": neutral,
                "negative": negative,
                "sla_violations": sla_violations,
                "sla_warnings": sla_warnings,
            },
            "platform_stats": platform_stats,
            "rating_distribution": rating_dist,
            "trend": {
                "recent_avg": round(recent_avg, 1),
                "previous_avg": round(previous_avg, 1),
                "recent_count": len(recent),
                "previous_count": len(previous),
                "direction": "up" if recent_avg > previous_avg else "down" if recent_avg < previous_avg else "flat",
            },
            "platform_targets": {k: v for k, v in review_agent.PLATFORMS.items()},
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/actions/{action_id}")
async def delete_review_action(action_id: str):
    """Delete a single review action by UUID"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        sb.table("agent_actions").delete().eq("id", action_id).execute()
        return {"success": True, "message": f"Action {action_id} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import")
async def import_review(data: Dict[str, Any] = Body(...)):
    """Import a single review manually"""
    from shared.database import get_supabase
    from datetime import datetime

    platform = data.get("platform", "").strip()
    rating = data.get("rating")
    author = data.get("author", "").strip()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    review_url = data.get("review_url", "").strip()

    if not platform or not rating or not content:
        raise HTTPException(status_code=400, detail="platform, rating, and content are required")

    try:
        sb = get_supabase()
        record = {
            "platform": platform,
            "rating": int(rating),
            "author": author or "Anonymous",
            "title": title,
            "content": content,
            "review_url": review_url or None,
            "responded": False,
            "created_at": datetime.utcnow().isoformat(),
            "review_date": data.get("review_date") or datetime.utcnow().isoformat(),
        }
        result = sb.table("reviews").insert(record).execute()
        return {"success": True, "review": result.data[0] if result.data else record}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/batch")
async def import_reviews_batch(data: Dict[str, Any] = Body(...)):
    """Import multiple reviews at once"""
    from shared.database import get_supabase
    from datetime import datetime

    reviews_list = data.get("reviews", [])
    if not reviews_list:
        raise HTTPException(status_code=400, detail="reviews array is required")

    try:
        sb = get_supabase()
        records = []
        for r in reviews_list:
            records.append({
                "platform": r.get("platform", "Unknown"),
                "rating": int(r.get("rating", 3)),
                "author": r.get("author", "Anonymous"),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "review_url": r.get("review_url") or None,
                "responded": r.get("responded", False),
                "response_text": r.get("response_text") or None,
                "created_at": datetime.utcnow().isoformat(),
                "review_date": r.get("review_date") or datetime.utcnow().isoformat(),
            })

        result = sb.table("reviews").insert(records).execute()
        return {"success": True, "imported": len(result.data or records)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/reviews/{review_id}/respond")
async def mark_review_responded(review_id: str, data: Dict[str, Any] = Body(...)):
    """Mark a review as responded with the response text"""
    from shared.database import get_supabase
    from datetime import datetime

    response_text = data.get("response_text", "").strip()
    if not response_text:
        raise HTTPException(status_code=400, detail="response_text is required")

    try:
        sb = get_supabase()
        sb.table("reviews").update({
            "responded": True,
            "response_text": response_text,
            "responded_at": datetime.utcnow().isoformat(),
        }).eq("id", review_id).execute()
        return {"success": True, "message": f"Review {review_id} marked as responded"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/reviews/{review_id}")
async def delete_review(review_id: str):
    """Delete a review"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        sb.table("reviews").delete().eq("id", review_id).execute()
        return {"success": True, "message": f"Review {review_id} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
