from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from agents.ads import ads_agent

router = APIRouter()


class RSARequest(BaseModel):
    campaign: str
    ad_group: str
    target_keyword: Optional[str] = None


class BidOptimizationRequest(BaseModel):
    campaign_id: str
    performance_data: Dict[str, Any]


class NegativeKeywordRequest(BaseModel):
    search_terms_report: List[Dict[str, Any]]


class CampaignCreateRequest(BaseModel):
    campaign_type: str  # brand, core_product, churn_prevention, etc.


@router.get("/status")
async def get_status():
    """Get Google Ads agent status"""
    return {
        "agent": "ads",
        "status": "operational",
        "campaigns": list(ads_agent.CAMPAIGN_STRUCTURE.keys()),
        "optimization_rules": len(ads_agent.OPTIMIZATION_RULES),
        "rsa_headline_bank": len(ads_agent.RSA_HEADLINE_BANK)
    }


@router.get("/campaigns")
async def get_campaigns():
    """Get all campaign configurations"""
    return {
        "campaigns": ads_agent.CAMPAIGN_STRUCTURE
    }


@router.post("/campaigns/create")
async def create_campaign(request: CampaignCreateRequest):
    """Create a new Google Ads campaign"""
    try:
        result = await ads_agent.create_campaign(request.campaign_type)
        return {"success": True, "campaign": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rsa/generate")
async def generate_rsa(request: RSARequest):
    """Generate Responsive Search Ad variants"""
    try:
        result = await ads_agent.generate_rsa(
            campaign=request.campaign,
            ad_group=request.ad_group,
            target_keyword=request.target_keyword
        )
        return {"success": True, "rsa": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize")
async def optimize_campaigns():
    """Quick optimize all campaigns"""
    try:
        results = await ads_agent.run_daily_optimization()
        return {"success": True, "message": "Campaign optimization started", "results": results}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/optimize/bids")
async def optimize_bids(request: BidOptimizationRequest):
    """Optimize bids based on performance data"""
    try:
        result = await ads_agent.optimize_bids(
            campaign_id=request.campaign_id,
            performance_data=request.performance_data
        )
        return {"success": True, "optimizations": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/negative-keywords/harvest")
async def harvest_negative_keywords(request: NegativeKeywordRequest):
    """Harvest negative keywords from search terms report"""
    try:
        negative_keywords = await ads_agent.harvest_negative_keywords(
            search_terms_report=request.search_terms_report
        )
        return {
            "success": True,
            "negative_keywords": negative_keywords,
            "count": len(negative_keywords)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}/analyze")
async def analyze_campaign(campaign_id: str, date_range: int = 30):
    """Analyze campaign performance"""
    try:
        analysis = await ads_agent.analyze_campaign_performance(
            campaign_id=campaign_id,
            date_range=date_range
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize/daily")
async def run_daily_optimization(background_tasks: BackgroundTasks):
    """Run daily optimization routine"""
    try:
        background_tasks.add_task(ads_agent.run_daily_optimization)
        return {
            "success": True,
            "message": "Daily optimization started in background"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize/daily/sync")
async def run_daily_optimization_sync():
    """Run daily optimization synchronously (for testing)"""
    try:
        results = await ads_agent.run_daily_optimization()
        return {"success": True, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rsa/headline-bank")
async def get_headline_bank():
    """Get RSA headline bank"""
    return {
        "headlines": ads_agent.RSA_HEADLINE_BANK,
        "descriptions": ads_agent.RSA_DESCRIPTION_BANK
    }


@router.get("/optimization-rules")
async def get_optimization_rules():
    """Get all optimization rules"""
    return {
        "rules": ads_agent.OPTIMIZATION_RULES
    }
