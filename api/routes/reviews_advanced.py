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
