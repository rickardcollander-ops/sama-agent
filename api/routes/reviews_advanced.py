"""
Advanced Review API Routes
Web scraping for G2, Capterra, Trustpilot, TrustRadius, Software Advice
Competitor intelligence and prospect finding
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
    - trustradius
    - software_advice
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
        elif request.platform.lower() == "trustradius":
            slug = request.url or "successifier"
            result = await review_scraper.scrape_trustradius_reviews(slug)
        elif request.platform.lower() in ("software_advice", "softwareadvice"):
            slug = request.url or "successifier"
            result = await review_scraper.scrape_software_advice_reviews(slug)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown platform: {request.platform}")

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to scrape reviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scrape/all")
async def scrape_all_platforms():
    """
    Scrape reviews from all configured platforms

    Platforms: G2, Capterra, Trustpilot, TrustRadius, Software Advice
    """
    try:
        result = await review_scraper.scrape_all_platforms()
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"Failed to scrape all platforms: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sla/check")
async def check_sla_compliance():
    """Check review response SLA compliance"""
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


# ── Competitor Intelligence ──────────────────────────────────────────────────


@router.post("/competitor/monitor/{competitor}")
async def monitor_competitor(competitor: str):
    """
    Monitor competitor reviews with AI-powered analysis

    Competitors: gainsight, totango, churnzero, planhat, vitally, clientsuccess, custify
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
    """Monitor all configured competitors with competitive intelligence"""
    try:
        from agents.review_competitor import competitor_monitor
        result = await competitor_monitor.monitor_all_competitors()
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        return result
    except Exception as e:
        logger.error(f"Failed to monitor all competitors: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/competitor/snapshot")
async def get_competitive_snapshot():
    """Get latest snapshot of all competitor ratings and key metrics"""
    try:
        from agents.review_competitor import competitor_monitor
        result = await competitor_monitor.get_competitive_snapshot()
        return result
    except Exception as e:
        logger.error(f"Failed to get competitive snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/competitor/history/{competitor}")
async def get_competitor_history(competitor: str, days: int = 90):
    """Get historical competitor review data for trend analysis"""
    try:
        from agents.review_competitor import competitor_monitor
        result = await competitor_monitor.get_competitor_history(competitor, days)
        return result
    except Exception as e:
        logger.error(f"Failed to get competitor history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/competitor/list")
async def list_competitors():
    """List all configured competitors"""
    from agents.review_competitor import competitor_monitor
    return {
        "competitors": {
            key: {"name": comp["name"], "category": comp.get("category", "")}
            for key, comp in competitor_monitor.COMPETITORS.items()
        }
    }


# ── Prospect Finder ─────────────────────────────────────────────────────────


@router.post("/prospects/find")
async def find_prospects(competitor: Optional[str] = None):
    """
    Find potential customers from competitor review data.
    Uses AI to identify dissatisfied users and build prospect profiles.
    """
    try:
        from agents.review_prospect_finder import prospect_finder
        result = await prospect_finder.find_prospects_from_reviews(competitor)
        return result
    except Exception as e:
        logger.error(f"Failed to find prospects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/prospects/latest")
async def get_latest_prospects():
    """Get the most recent prospect analysis"""
    try:
        from agents.review_prospect_finder import prospect_finder
        result = await prospect_finder.get_latest_prospects()
        return result
    except Exception as e:
        logger.error(f"Failed to get latest prospects: {e}")
        raise HTTPException(status_code=500, detail=str(e))
