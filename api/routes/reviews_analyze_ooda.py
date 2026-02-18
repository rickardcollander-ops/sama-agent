"""
Reviews Agent /analyze endpoint with OODA loop implementation
"""

from typing import Dict, Any
from shared.ooda_templates import run_agent_ooda_cycle, create_analysis_structure, add_pattern, add_anomaly, create_action
from shared.database import get_supabase
from agents.reviews import review_agent


async def run_reviews_analysis_with_ooda() -> Dict[str, Any]:
    """Run Reviews analysis using OODA loop"""
    
    async def observe():
        """OBSERVE: Fetch reviews data"""
        observations = {}
        sb = get_supabase()
        
        # Fetch all reviews
        try:
            result = sb.table("reviews").select("*").order("created_at", desc=True).limit(100).execute()
            observations["reviews"] = result.data or []
        except Exception:
            observations["reviews"] = []
        
        observations["platforms"] = review_agent.PLATFORMS
        observations["competitors"] = ["gainsight", "totango", "churnzero"]
        
        return observations
    
    async def orient(observations):
        """ORIENT: Analyze review patterns and opportunities"""
        analysis = create_analysis_structure()
        
        reviews = observations.get("reviews", [])
        platforms = observations.get("platforms", {})
        
        # Analyze unresponded reviews
        unresponded = [r for r in reviews if not r.get("responded", False)]
        if unresponded:
            critical_unresponded = [r for r in unresponded if r.get("rating", 5) <= 2]
            if critical_unresponded:
                add_anomaly(analysis, "critical_unresponded_reviews", "critical", {"count": len(critical_unresponded)})
            else:
                add_pattern(analysis, "unresponded_reviews", {"count": len(unresponded)})
        
        # Analyze negative review trends
        negative_reviews = [r for r in reviews if r.get("rating", 5) <= 2]
        if len(negative_reviews) >= 3:
            add_anomaly(analysis, "negative_review_trend", "high", {"count": len(negative_reviews)})
        
        # Analyze platform coverage
        platform_counts = {}
        for review in reviews:
            p = review.get("platform", "unknown")
            platform_counts[p] = platform_counts.get(p, 0) + 1
        
        for platform_key, platform_info in platforms.items():
            current = platform_counts.get(platform_info["name"], platform_counts.get(platform_key, 0))
            target = platform_info.get("target_reviews", 50)
            if current < target:
                gap = target - current
                if gap > 20:
                    add_pattern(analysis, "platform_review_gap", {"platform": platform_info["name"], "gap": gap})
        
        # Calculate average rating
        if reviews:
            avg_rating = sum(r.get("rating", 0) for r in reviews) / len(reviews)
            if avg_rating < 4.0:
                add_anomaly(analysis, "low_average_rating", "high", {"rating": avg_rating})
        
        return analysis
    
    async def decide(analysis, observations):
        """DECIDE: Generate review management actions"""
        actions = []
        reviews = observations.get("reviews", [])
        platforms = observations.get("platforms", {})
        competitors = observations.get("competitors", [])
        
        # Actions for unresponded reviews
        for review in reviews:
            if not review.get("responded", False):
                rating = review.get("rating", 5)
                author = review.get("author", "Unknown")
                platform = review.get("platform", "unknown")
                title = review.get("title", "")
                content = review.get("content", "")
                review_id = str(review.get("id", ""))
                
                priority = "critical" if rating <= 2 else "high" if rating == 3 else "medium"
                
                actions.append(create_action(
                    f"review-respond-{review_id[:20]}",
                    "respond",
                    priority,
                    f"Respond to {rating}-star review by {author} on {platform}",
                    f"{title}: {content[:150]}...",
                    f"Generate and post a {'empathetic, solution-oriented' if rating <= 3 else 'grateful'} response",
                    {"type": "customer_satisfaction", "expected_sentiment_improvement": 1 if rating <= 3 else 0},
                    review={
                        "id": review_id,
                        "text": content,
                        "rating": rating,
                        "reviewer": author,
                        "platform": platform,
                        "title": title
                    }
                ))
        
        # Actions for platform coverage gaps
        platform_counts = {}
        for review in reviews:
            p = review.get("platform", "unknown")
            platform_counts[p] = platform_counts.get(p, 0) + 1
        
        for platform_key, platform_info in platforms.items():
            current = platform_counts.get(platform_info["name"], platform_counts.get(platform_key, 0))
            target = platform_info.get("target_reviews", 50)
            if current < target:
                gap = target - current
                actions.append(create_action(
                    f"review-request-{platform_key}",
                    "request_reviews",
                    "high" if gap > 20 else "medium",
                    f"Request reviews on {platform_info['name']} ({current}/{target})",
                    f"Need {gap} more reviews to reach target. Current: {current}, Target: {target}.",
                    f"Generate personalized review request emails for happy customers on {platform_info['name']}",
                    {"type": "social_proof", "expected_reviews": min(gap, 10)},
                    platform=platform_key
                ))
        
        # Action for negative review trend analysis
        negative_reviews = [r for r in reviews if r.get("rating", 5) <= 2]
        if len(negative_reviews) >= 3:
            actions.append(create_action(
                "review-negative-trend",
                "analyze_sentiment",
                "high",
                f"Negative review trend: {len(negative_reviews)} low-rated reviews",
                "Multiple negative reviews detected. Analyze common themes and address root causes.",
                "Run sentiment analysis on negative reviews to identify patterns",
                {"type": "product_improvement", "expected_insights": 3},
                reviews=[{
                    "text": r.get("content", ""),
                    "rating": r.get("rating", 0),
                    "platform": r.get("platform", "")
                } for r in negative_reviews[:10]]
            ))
        
        # Actions for competitor analysis
        for comp in competitors:
            actions.append(create_action(
                f"review-competitor-{comp}",
                "competitor_analysis",
                "low",
                f"Analyze {comp.title()} reviews",
                "Monitor competitor reviews to find positioning opportunities.",
                f"Analyze recent {comp.title()} reviews for common complaints we can address",
                {"type": "competitive_intelligence", "expected_opportunities": 2},
                competitor=comp
            ))
        
        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
        
        return actions
    
    return await run_agent_ooda_cycle("reviews", observe, orient, decide)
