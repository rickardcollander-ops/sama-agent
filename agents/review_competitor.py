"""
Competitor Review Monitoring
Tracks competitor reviews on G2, Capterra, Trustpilot
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging

from agents.review_scraper import review_scraper
from shared.database import get_supabase

logger = logging.getLogger(__name__)


class CompetitorReviewMonitor:
    """Monitor competitor reviews and sentiment"""
    
    COMPETITORS = {
        "gainsight": {
            "name": "Gainsight",
            "g2_url": "https://www.g2.com/products/gainsight-cs",
            "capterra_url": "https://www.capterra.com/p/gainsight",
            "trustpilot": "gainsight"
        },
        "totango": {
            "name": "Totango",
            "g2_url": "https://www.g2.com/products/totango",
            "capterra_url": "https://www.capterra.com/p/totango",
            "trustpilot": "totango"
        },
        "churnzero": {
            "name": "ChurnZero",
            "g2_url": "https://www.g2.com/products/churnzero",
            "capterra_url": "https://www.capterra.com/p/churnzero",
            "trustpilot": "churnzero"
        },
        "planhat": {
            "name": "Planhat",
            "g2_url": "https://www.g2.com/products/planhat",
            "capterra_url": "https://www.capterra.com/p/planhat",
            "trustpilot": "planhat"
        }
    }
    
    def __init__(self):
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def monitor_competitor(self, competitor_key: str) -> Dict[str, Any]:
        """
        Monitor a specific competitor's reviews
        
        Args:
            competitor_key: Competitor identifier (e.g., "gainsight")
        """
        if competitor_key not in self.COMPETITORS:
            return {
                "success": False,
                "error": f"Unknown competitor: {competitor_key}"
            }
        
        competitor = self.COMPETITORS[competitor_key]
        
        try:
            results = {}
            
            # Scrape G2
            if competitor.get("g2_url"):
                g2_result = await review_scraper.scrape_g2_reviews(competitor["g2_url"])
                results["g2"] = g2_result
            
            # Scrape Capterra
            if competitor.get("capterra_url"):
                capterra_result = await review_scraper.scrape_capterra_reviews(competitor["capterra_url"])
                results["capterra"] = capterra_result
            
            # Scrape Trustpilot
            if competitor.get("trustpilot"):
                trustpilot_result = await review_scraper.scrape_trustpilot_reviews(competitor["trustpilot"])
                results["trustpilot"] = trustpilot_result
            
            # Aggregate data
            total_reviews = sum(r.get("reviews_scraped", 0) for r in results.values())
            avg_rating = sum(
                r.get("overall_rating", 0) * r.get("review_count", 0) 
                for r in results.values()
            ) / sum(r.get("review_count", 1) for r in results.values())
            
            # Extract common complaints and praises
            all_reviews = []
            for platform_result in results.values():
                all_reviews.extend(platform_result.get("reviews", []))
            
            complaints = self._extract_complaints(all_reviews)
            praises = self._extract_praises(all_reviews)
            
            # Save to database
            sb = self._get_sb()
            sb.table("competitor_reviews").insert({
                "competitor": competitor["name"],
                "competitor_key": competitor_key,
                "total_reviews_scraped": total_reviews,
                "avg_rating": round(avg_rating, 2),
                "platforms": results,
                "common_complaints": complaints,
                "common_praises": praises,
                "monitored_at": datetime.utcnow().isoformat()
            }).execute()
            
            return {
                "success": True,
                "competitor": competitor["name"],
                "total_reviews": total_reviews,
                "avg_rating": round(avg_rating, 2),
                "platforms": results,
                "insights": {
                    "common_complaints": complaints,
                    "common_praises": praises
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to monitor competitor {competitor_key}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def monitor_all_competitors(self) -> Dict[str, Any]:
        """Monitor all configured competitors"""
        results = {}
        
        for competitor_key in self.COMPETITORS.keys():
            result = await self.monitor_competitor(competitor_key)
            results[competitor_key] = result
        
        # Generate competitive analysis
        analysis = self._generate_competitive_analysis(results)
        
        return {
            "success": True,
            "competitors_monitored": len(results),
            "results": results,
            "competitive_analysis": analysis
        }
    
    def _extract_complaints(self, reviews: List[Dict[str, Any]]) -> List[str]:
        """Extract common complaints from reviews"""
        # Simple keyword-based extraction
        complaint_keywords = [
            "expensive", "costly", "price", "pricing",
            "difficult", "hard", "complex", "complicated",
            "slow", "laggy", "performance",
            "support", "customer service", "help",
            "bug", "error", "crash", "issue"
        ]
        
        complaints = []
        for review in reviews:
            if review.get("rating", 5) <= 3:
                text = review.get("text", "").lower()
                for keyword in complaint_keywords:
                    if keyword in text:
                        # Extract sentence containing keyword
                        sentences = text.split('.')
                        for sentence in sentences:
                            if keyword in sentence:
                                complaints.append(sentence.strip())
                                break
        
        # Return top 5 unique complaints
        return list(set(complaints))[:5]
    
    def _extract_praises(self, reviews: List[Dict[str, Any]]) -> List[str]:
        """Extract common praises from reviews"""
        praise_keywords = [
            "great", "excellent", "amazing", "fantastic",
            "easy", "simple", "intuitive",
            "helpful", "support", "responsive",
            "feature", "functionality",
            "value", "worth"
        ]
        
        praises = []
        for review in reviews:
            if review.get("rating", 3) >= 4:
                text = review.get("text", "").lower()
                for keyword in praise_keywords:
                    if keyword in text:
                        sentences = text.split('.')
                        for sentence in sentences:
                            if keyword in sentence:
                                praises.append(sentence.strip())
                                break
        
        return list(set(praises))[:5]
    
    def _generate_competitive_analysis(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate competitive analysis from all competitor data"""
        # Extract ratings
        ratings = {}
        for competitor_key, result in results.items():
            if result.get("success"):
                ratings[self.COMPETITORS[competitor_key]["name"]] = result.get("avg_rating", 0)
        
        # Find strengths and weaknesses
        all_complaints = []
        all_praises = []
        
        for result in results.values():
            if result.get("success") and result.get("insights"):
                all_complaints.extend(result["insights"].get("common_complaints", []))
                all_praises.extend(result["insights"].get("common_praises", []))
        
        return {
            "competitor_ratings": ratings,
            "market_avg_rating": sum(ratings.values()) / len(ratings) if ratings else 0,
            "common_competitor_weaknesses": list(set(all_complaints))[:10],
            "common_competitor_strengths": list(set(all_praises))[:10],
            "opportunities": self._identify_opportunities(all_complaints),
            "threats": self._identify_threats(all_praises)
        }
    
    def _identify_opportunities(self, complaints: List[str]) -> List[str]:
        """Identify opportunities based on competitor complaints"""
        opportunities = []
        
        # Map complaints to opportunities
        if any("price" in c or "expensive" in c for c in complaints):
            opportunities.append("Competitive pricing advantage")
        
        if any("complex" in c or "difficult" in c for c in complaints):
            opportunities.append("Emphasize ease of use and quick setup")
        
        if any("support" in c for c in complaints):
            opportunities.append("Highlight superior customer support")
        
        if any("slow" in c or "performance" in c for c in complaints):
            opportunities.append("Promote fast, responsive platform")
        
        return opportunities
    
    def _identify_threats(self, praises: List[str]) -> List[str]:
        """Identify threats based on competitor strengths"""
        threats = []
        
        if any("feature" in p for p in praises):
            threats.append("Competitors have strong feature sets")
        
        if any("support" in p for p in praises):
            threats.append("Competitors have good customer support")
        
        if any("easy" in p or "simple" in p for p in praises):
            threats.append("Competitors are user-friendly")
        
        return threats


# Global instance
competitor_monitor = CompetitorReviewMonitor()
