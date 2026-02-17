"""
Advanced Review API Routes
Web scraping for G2, Capterra, Trustpilot
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from agents.review_scraper import review_scraper

router = APIRouter()
logger = logging.getLogger(__name__)


class ScrapeRequest(BaseModel):
    platform: str
    url: Optional[str] = None


@router.post("/scrape")
async def scrape_reviews(request: ScrapeRequest):
    """
    Scrape reviews from a specific platform
    
    Platforms:
    - g2
    - capterra
    - trustpilot
    """
    try:
        result = None
        
        if request.platform.lower() == "g2":
            url = request.url or "https://www.g2.com/products/successifier"
            result = await review_scraper.scrape_g2_reviews(url)
        elif request.platform.lower() == "capterra":
            url = request.url or "https://www.capterra.com/p/successifier"
            result = await review_scraper.scrape_capterra_reviews(url)
        elif request.platform.lower() == "trustpilot":
            company = request.url or "successifier"
            result = await review_scraper.scrape_trustpilot_reviews(company)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown platform: {request.platform}")
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to scrape reviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scrape/all")
async def scrape_all_platforms():
    """
    Scrape reviews from all configured platforms
    
    Platforms:
    - G2
    - Capterra
    - Trustpilot
    """
    try:
        result = await review_scraper.scrape_all_platforms()
        
        return {
            "success": True,
            **result
        }
        
    except Exception as e:
        logger.error(f"Failed to scrape all platforms: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sla/check")
async def check_sla_compliance():
    """
    Check review response SLA compliance
    
    SLA Thresholds:
    - 5-star: 24 hours
    - 4-star: 12 hours
    - 3-star: 6 hours
    - 2-star: 3 hours
    - 1-star: 2 hours
    """
    try:
        from agents.review_sla import review_sla_tracker
        
        result = await review_sla_tracker.check_sla_compliance()
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to check SLA compliance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sla/stats")
async def get_sla_stats(days: int = 30):
    """Get SLA statistics for the past N days"""
    try:
        from agents.review_sla import review_sla_tracker
        
        result = await review_sla_tracker.get_sla_stats(days)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get SLA stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/competitor/monitor/{competitor}")
async def monitor_competitor(competitor: str):
    """
    Monitor competitor reviews
    
    Competitors:
    - gainsight
    - totango
    - churnzero
    - planhat
    """
    try:
        from agents.review_competitor import competitor_monitor
        
        result = await competitor_monitor.monitor_competitor(competitor)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to monitor competitor: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/competitor/monitor-all")
async def monitor_all_competitors():
    """Monitor all configured competitors"""
    try:
        from agents.review_competitor import competitor_monitor
        
        result = await competitor_monitor.monitor_all_competitors()
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to monitor all competitors: {e}")
        raise HTTPException(status_code=500, detail=str(e))
