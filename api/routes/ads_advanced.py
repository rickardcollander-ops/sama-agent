"""
Advanced Ads API Routes
Performance Max campaigns, device bid adjustments, ad copy analysis
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from agents.ads_advanced import advanced_ads_manager

router = APIRouter()
logger = logging.getLogger(__name__)


class PerformanceMaxRequest(BaseModel):
    campaign_name: str
    budget: float
    target_roas: Optional[float] = None


class DeviceBidRequest(BaseModel):
    campaign_id: str
    mobile_adjustment: float = 1.0
    tablet_adjustment: float = 1.0
    desktop_adjustment: float = 1.0


class AdCopyAnalysisRequest(BaseModel):
    ad_group_id: str


@router.post("/performance-max/create")
async def create_performance_max_campaign(request: PerformanceMaxRequest):
    """
    Create Performance Max campaign
    
    Performance Max uses Google's AI to optimize across all channels
    """
    try:
        result = await advanced_ads_manager.create_performance_max_campaign(
            campaign_name=request.campaign_name,
            budget=request.budget,
            target_roas=request.target_roas
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to create Performance Max campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/device-bids")
async def set_device_bid_adjustments(request: DeviceBidRequest):
    """
    Set device-specific bid adjustments
    
    Adjust bids based on device performance:
    - mobile_adjustment: 1.2 = +20% for mobile
    - tablet_adjustment: 0.8 = -20% for tablet
    - desktop_adjustment: 1.0 = no change
    """
    try:
        result = await advanced_ads_manager.set_device_bid_adjustments(
            campaign_id=request.campaign_id,
            mobile_adjustment=request.mobile_adjustment,
            tablet_adjustment=request.tablet_adjustment,
            desktop_adjustment=request.desktop_adjustment
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to set device bid adjustments: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ad-copy/analyze")
async def analyze_ad_copy_rotation(request: AdCopyAnalysisRequest):
    """
    Analyze ad copy performance to identify winners
    
    Returns:
    - Winner (highest CTR with significant impressions)
    - Underperformers (low CTR ads to pause)
    - Recommendations
    """
    try:
        result = await advanced_ads_manager.analyze_ad_copy_rotation(request.ad_group_id)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to analyze ad copy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cpc-spikes/check")
async def check_cpc_spikes():
    """
    Check for CPC spikes and create alerts
    
    Compares last 7 days vs previous 7 days
    Creates alerts for campaigns with >30% CPC increase
    """
    try:
        alerts = await advanced_ads_manager.check_cpc_spikes()
        
        return {
            "success": True,
            "alerts_created": len(alerts),
            "alerts": [
                {
                    "campaign": alert.data.get("campaign"),
                    "spike_percentage": alert.data.get("spike_percentage"),
                    "current_cpc": alert.data.get("current_cpc"),
                    "avg_cpc": alert.data.get("avg_cpc")
                }
                for alert in alerts
            ]
        }
        
    except Exception as e:
        logger.error(f"Failed to check CPC spikes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-daily")
async def run_daily_optimization():
    """
    Run daily automated optimization
    
    Tasks:
    - Check CPC spikes
    - Analyze ad copy performance
    - Adjust bids based on performance
    - Pause underperforming ads
    """
    try:
        results = {}
        
        # Check CPC spikes
        alerts = await advanced_ads_manager.check_cpc_spikes()
        results["cpc_alerts"] = len(alerts)
        
        # TODO: Add more optimization tasks
        # - Bid adjustments
        # - Budget reallocation
        # - Negative keyword harvesting
        
        return {
            "success": True,
            "message": "Daily optimization completed",
            **results
        }
        
    except Exception as e:
        logger.error(f"Daily optimization failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
