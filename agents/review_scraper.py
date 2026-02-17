"""
Web Scraping for Review Platforms
G2, Capterra, Trustpilot - scrapes public review data
"""

from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup
import re
from datetime import datetime
import logging

from shared.rate_limiter import rate_limit
from shared.database import get_supabase

logger = logging.getLogger(__name__)


class ReviewScraper:
    """Scrape reviews from public review platforms"""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def scrape_g2_reviews(self, product_url: str) -> Dict[str, Any]:
        """
        Scrape reviews from G2
        
        Args:
            product_url: G2 product page URL (e.g., https://www.g2.com/products/successifier)
        """
        if not await rate_limit("g2_scraping"):
            return {"success": False, "error": "Rate limit exceeded"}
        
        try:
            response = await self.http_client.get(product_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract overall rating
            rating_elem = soup.find('div', class_='fw-semibold')
            overall_rating = float(rating_elem.text.strip()) if rating_elem else 0.0
            
            # Extract review count
            review_count_elem = soup.find('span', string=re.compile(r'\d+ Reviews'))
            review_count = 0
            if review_count_elem:
                match = re.search(r'(\d+)', review_count_elem.text)
                if match:
                    review_count = int(match.group(1))
            
            # Extract recent reviews
            reviews = []
            review_cards = soup.find_all('div', class_='paper')[:10]  # Get latest 10
            
            for card in review_cards:
                try:
                    # Extract rating
                    stars = len(card.find_all('div', class_='stars'))
                    
                    # Extract title
                    title_elem = card.find('h3')
                    title = title_elem.text.strip() if title_elem else ""
                    
                    # Extract review text
                    text_elem = card.find('div', itemprop='reviewBody')
                    text = text_elem.text.strip() if text_elem else ""
                    
                    # Extract author
                    author_elem = card.find('span', itemprop='author')
                    author = author_elem.text.strip() if author_elem else "Anonymous"
                    
                    # Extract date
                    date_elem = card.find('time')
                    date_str = date_elem.get('datetime') if date_elem else None
                    
                    reviews.append({
                        "rating": stars,
                        "title": title,
                        "text": text,
                        "author": author,
                        "date": date_str,
                        "platform": "G2"
                    })
                    
                except Exception as e:
                    logger.warning(f"Failed to parse G2 review card: {e}")
                    continue
            
            # Save to database
            sb = self._get_sb()
            for review in reviews:
                sb.table("reviews").insert({
                    **review,
                    "product": "Successifier",
                    "scraped_at": datetime.utcnow().isoformat()
                }).execute()
            
            return {
                "success": True,
                "platform": "G2",
                "overall_rating": overall_rating,
                "review_count": review_count,
                "reviews_scraped": len(reviews),
                "reviews": reviews
            }
            
        except Exception as e:
            logger.error(f"Failed to scrape G2 reviews: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def scrape_capterra_reviews(self, product_url: str) -> Dict[str, Any]:
        """
        Scrape reviews from Capterra
        
        Args:
            product_url: Capterra product page URL
        """
        if not await rate_limit("capterra_scraping"):
            return {"success": False, "error": "Rate limit exceeded"}
        
        try:
            response = await self.http_client.get(product_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract overall rating
            rating_elem = soup.find('span', class_='overall-rating')
            overall_rating = float(rating_elem.text.strip()) if rating_elem else 0.0
            
            # Extract review count
            count_elem = soup.find('span', class_='review-count')
            review_count = 0
            if count_elem:
                match = re.search(r'(\d+)', count_elem.text)
                if match:
                    review_count = int(match.group(1))
            
            # Extract recent reviews
            reviews = []
            review_cards = soup.find_all('div', class_='review-card')[:10]
            
            for card in review_cards:
                try:
                    # Extract rating
                    rating_elem = card.find('div', class_='rating-value')
                    rating = float(rating_elem.text.strip()) if rating_elem else 0.0
                    
                    # Extract title
                    title_elem = card.find('h3', class_='review-title')
                    title = title_elem.text.strip() if title_elem else ""
                    
                    # Extract review text
                    text_elem = card.find('p', class_='review-text')
                    text = text_elem.text.strip() if text_elem else ""
                    
                    # Extract author
                    author_elem = card.find('span', class_='reviewer-name')
                    author = author_elem.text.strip() if author_elem else "Anonymous"
                    
                    # Extract date
                    date_elem = card.find('span', class_='review-date')
                    date_str = date_elem.text.strip() if date_elem else None
                    
                    reviews.append({
                        "rating": int(rating),
                        "title": title,
                        "text": text,
                        "author": author,
                        "date": date_str,
                        "platform": "Capterra"
                    })
                    
                except Exception as e:
                    logger.warning(f"Failed to parse Capterra review card: {e}")
                    continue
            
            # Save to database
            sb = self._get_sb()
            for review in reviews:
                sb.table("reviews").insert({
                    **review,
                    "product": "Successifier",
                    "scraped_at": datetime.utcnow().isoformat()
                }).execute()
            
            return {
                "success": True,
                "platform": "Capterra",
                "overall_rating": overall_rating,
                "review_count": review_count,
                "reviews_scraped": len(reviews),
                "reviews": reviews
            }
            
        except Exception as e:
            logger.error(f"Failed to scrape Capterra reviews: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def scrape_trustpilot_reviews(self, company_name: str) -> Dict[str, Any]:
        """
        Scrape reviews from Trustpilot
        
        Args:
            company_name: Company name on Trustpilot (e.g., "successifier")
        """
        if not await rate_limit("trustpilot_scraping"):
            return {"success": False, "error": "Rate limit exceeded"}
        
        try:
            url = f"https://www.trustpilot.com/review/{company_name}"
            response = await self.http_client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract overall rating (TrustScore)
            rating_elem = soup.find('p', class_='typography_body-l__KUYFJ')
            overall_rating = 0.0
            if rating_elem:
                match = re.search(r'(\d+\.?\d*)', rating_elem.text)
                if match:
                    overall_rating = float(match.group(1))
            
            # Extract review count
            count_elem = soup.find('p', string=re.compile(r'\d+ reviews'))
            review_count = 0
            if count_elem:
                match = re.search(r'(\d+)', count_elem.text)
                if match:
                    review_count = int(match.group(1).replace(',', ''))
            
            # Extract recent reviews
            reviews = []
            review_cards = soup.find_all('article', class_='review')[:10]
            
            for card in review_cards:
                try:
                    # Extract rating (count stars)
                    stars = len(card.find_all('img', alt=re.compile(r'Rated \d')))
                    
                    # Extract title
                    title_elem = card.find('h2', class_='typography_heading-s')
                    title = title_elem.text.strip() if title_elem else ""
                    
                    # Extract review text
                    text_elem = card.find('p', class_='typography_body-l')
                    text = text_elem.text.strip() if text_elem else ""
                    
                    # Extract author
                    author_elem = card.find('span', class_='typography_heading-xxs')
                    author = author_elem.text.strip() if author_elem else "Anonymous"
                    
                    # Extract date
                    date_elem = card.find('time')
                    date_str = date_elem.get('datetime') if date_elem else None
                    
                    reviews.append({
                        "rating": stars,
                        "title": title,
                        "text": text,
                        "author": author,
                        "date": date_str,
                        "platform": "Trustpilot"
                    })
                    
                except Exception as e:
                    logger.warning(f"Failed to parse Trustpilot review card: {e}")
                    continue
            
            # Save to database
            sb = self._get_sb()
            for review in reviews:
                sb.table("reviews").insert({
                    **review,
                    "product": "Successifier",
                    "scraped_at": datetime.utcnow().isoformat()
                }).execute()
            
            return {
                "success": True,
                "platform": "Trustpilot",
                "overall_rating": overall_rating,
                "review_count": review_count,
                "reviews_scraped": len(reviews),
                "reviews": reviews
            }
            
        except Exception as e:
            logger.error(f"Failed to scrape Trustpilot reviews: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def scrape_all_platforms(self) -> Dict[str, Any]:
        """Scrape reviews from all configured platforms"""
        results = {}
        
        # G2
        g2_result = await self.scrape_g2_reviews("https://www.g2.com/products/successifier")
        results["g2"] = g2_result
        
        # Capterra
        capterra_result = await self.scrape_capterra_reviews("https://www.capterra.com/p/successifier")
        results["capterra"] = capterra_result
        
        # Trustpilot
        trustpilot_result = await self.scrape_trustpilot_reviews("successifier")
        results["trustpilot"] = trustpilot_result
        
        # Calculate aggregate stats
        total_reviews = sum(r.get("reviews_scraped", 0) for r in results.values())
        avg_rating = sum(
            r.get("overall_rating", 0) * r.get("review_count", 0) 
            for r in results.values()
        ) / sum(r.get("review_count", 1) for r in results.values())
        
        return {
            "total_reviews_scraped": total_reviews,
            "average_rating": round(avg_rating, 2),
            "platforms": results,
            "timestamp": datetime.utcnow().isoformat()
        }


# Global instance
review_scraper = ReviewScraper()
