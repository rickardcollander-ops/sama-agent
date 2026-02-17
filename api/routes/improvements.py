"""
API Routes for Critical Improvements
GA4 Analytics, LinkedIn, Budget Optimization, Anomaly Detection
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


# ============= CONTENT ANALYTICS (GA4) =============

class ContentPerformanceRequest(BaseModel):
    url_path: str
    days: int = 30


@router.post("/content/analytics/performance")
async def get_content_performance(request: ContentPerformanceRequest):
    """
    Get GA4 performance metrics for content
    
    Returns pageviews, time on page, bounce rate, engagement rate, conversions
    """
    try:
        from agents.content_analytics import content_analytics
        
        result = await content_analytics.get_content_performance(
            request.url_path,
            request.days
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get content performance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content/analytics/top-performing")
async def get_top_performing_content(
    days: int = 30,
    limit: int = 10,
    metric: str = "pageviews"
):
    """
    Get top performing content
    
    Metrics: pageviews, engagement_rate, conversions, time_on_page
    """
    try:
        from agents.content_analytics import content_analytics
        
        result = await content_analytics.get_top_performing_content(days, limit, metric)
        
        return {
            "success": True,
            "total": len(result),
            "content": result
        }
        
    except Exception as e:
        logger.error(f"Failed to get top performing content: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content/analytics/underperforming")
async def get_underperforming_content(threshold: int = 100, days: int = 30):
    """Identify underperforming content that needs optimization"""
    try:
        from agents.content_analytics import content_analytics
        
        result = await content_analytics.identify_underperforming_content(threshold, days)
        
        return {
            "success": True,
            "total": len(result),
            "content": result
        }
        
    except Exception as e:
        logger.error(f"Failed to identify underperforming content: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= LINKEDIN INTEGRATION =============

class LinkedInPostRequest(BaseModel):
    content: str
    link_url: Optional[str] = None
    image_url: Optional[str] = None


class LinkedInGenerateRequest(BaseModel):
    topic: str
    style: str = "professional"
    include_hashtags: bool = True


@router.post("/social/linkedin/post")
async def create_linkedin_post(request: LinkedInPostRequest):
    """Create and publish LinkedIn post"""
    try:
        from agents.social_linkedin import linkedin_manager
        
        result = await linkedin_manager.create_post(
            request.content,
            request.link_url,
            request.image_url
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to create LinkedIn post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/social/linkedin/generate")
async def generate_linkedin_post(request: LinkedInGenerateRequest):
    """Generate LinkedIn post content using AI"""
    try:
        from agents.social_linkedin import linkedin_manager
        
        result = await linkedin_manager.generate_linkedin_post(
            request.topic,
            request.style,
            request.include_hashtags
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to generate LinkedIn post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/social/linkedin/analytics/{post_id}")
async def get_linkedin_analytics(post_id: str):
    """Get analytics for a LinkedIn post"""
    try:
        from agents.social_linkedin import linkedin_manager
        
        result = await linkedin_manager.get_post_analytics(post_id)
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get LinkedIn analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/social/linkedin/company-analytics")
async def get_linkedin_company_analytics(days: int = 30):
    """Get company page analytics"""
    try:
        from agents.social_linkedin import linkedin_manager
        
        result = await linkedin_manager.get_company_analytics(days)
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get company analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= BUDGET OPTIMIZATION =============

@router.get("/ads/budget/analyze")
async def analyze_campaign_performance(days: int = 7):
    """Analyze campaign performance for budget optimization"""
    try:
        from agents.ads_budget_optimizer import budget_optimizer
        
        result = await budget_optimizer.analyze_campaign_performance(days)
        
        return {
            "success": True,
            "campaigns": result
        }
        
    except Exception as e:
        logger.error(f"Failed to analyze campaigns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ads/budget/optimize")
async def optimize_budgets(total_budget: float):
    """
    Optimize budget allocation across campaigns
    
    Automatically reallocates from low-ROAS to high-ROAS campaigns
    """
    try:
        from agents.ads_budget_optimizer import budget_optimizer
        
        result = await budget_optimizer.optimize_budgets(total_budget)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to optimize budgets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BudgetApplyRequest(BaseModel):
    recommendations: List[dict]


@router.post("/ads/budget/apply")
async def apply_budget_changes(request: BudgetApplyRequest):
    """Apply approved budget changes"""
    try:
        from agents.ads_budget_optimizer import budget_optimizer
        
        result = await budget_optimizer.apply_budget_changes(request.recommendations)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to apply budget changes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ads/budget/history")
async def get_optimization_history(days: int = 30):
    """Get budget optimization history"""
    try:
        from agents.ads_budget_optimizer import budget_optimizer
        
        result = await budget_optimizer.get_optimization_history(days)
        
        return {
            "success": True,
            "optimizations": result
        }
        
    except Exception as e:
        logger.error(f"Failed to get optimization history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= ANOMALY DETECTION =============

@router.get("/analytics/anomalies/traffic")
async def detect_traffic_anomalies(days: int = 30):
    """Detect traffic anomalies (sessions, pageviews)"""
    try:
        from agents.analytics_anomaly import anomaly_detector
        
        result = await anomaly_detector.detect_traffic_anomalies(days)
        
        return {
            "success": True,
            "anomalies_detected": len(result),
            "anomalies": result
        }
        
    except Exception as e:
        logger.error(f"Failed to detect traffic anomalies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/anomalies/conversions")
async def detect_conversion_anomalies(days: int = 30):
    """Detect conversion rate anomalies"""
    try:
        from agents.analytics_anomaly import anomaly_detector
        
        result = await anomaly_detector.detect_conversion_anomalies(days)
        
        return {
            "success": True,
            "anomalies_detected": len(result),
            "anomalies": result
        }
        
    except Exception as e:
        logger.error(f"Failed to detect conversion anomalies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/anomalies/spend")
async def detect_spend_anomalies(days: int = 30):
    """Detect ad spend anomalies"""
    try:
        from agents.analytics_anomaly import anomaly_detector
        
        result = await anomaly_detector.detect_spend_anomalies(days)
        
        return {
            "success": True,
            "anomalies_detected": len(result),
            "anomalies": result
        }
        
    except Exception as e:
        logger.error(f"Failed to detect spend anomalies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analytics/anomalies/analyze")
async def analyze_anomaly_root_cause(anomaly: dict):
    """Analyze root cause of detected anomaly"""
    try:
        from agents.analytics_anomaly import anomaly_detector
        
        result = await anomaly_detector.analyze_root_cause(anomaly)
        
        return {
            "success": True,
            **result
        }
        
    except Exception as e:
        logger.error(f"Failed to analyze root cause: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/anomalies/history")
async def get_anomaly_history(days: int = 30):
    """Get historical anomalies"""
    try:
        from agents.analytics_anomaly import anomaly_detector
        
        result = await anomaly_detector.get_anomaly_history(days)
        
        return {
            "success": True,
            "total": len(result),
            "anomalies": result
        }
        
    except Exception as e:
        logger.error(f"Failed to get anomaly history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
