from fastapi import APIRouter, HTTPException
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


@router.post("/analyze")
async def analyze_reviews(request: ReviewAnalysisRequest):
    """Analyze reviews and extract insights"""
    try:
        analysis = await review_agent.analyze_reviews(
            reviews=request.reviews,
            platform=request.platform
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
