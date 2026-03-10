"""
Web Scraping for Review Platforms
G2, Capterra, Trustpilot, TrustRadius, Software Advice - scrapes public review data
"""

from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup
import re
from datetime import datetime
import logging
import json

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
            },
            follow_redirects=True
        )
        self.sb = None

    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb

    def _save_reviews(self, reviews: List[Dict[str, Any]], product: str = "Successifier"):
        """Save scraped reviews to database"""
        sb = self._get_sb()
        for review in reviews:
            sb.table("reviews").insert({
                **review,
                "product": product,
                "scraped_at": datetime.utcnow().isoformat()
            }).execute()

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
            review_cards = soup.find_all('div', class_='paper')[:10]

            for card in review_cards:
                try:
                    stars = len(card.find_all('div', class_='stars'))
                    title_elem = card.find('h3')
                    title = title_elem.text.strip() if title_elem else ""
                    text_elem = card.find('div', itemprop='reviewBody')
                    text = text_elem.text.strip() if text_elem else ""
                    author_elem = card.find('span', itemprop='author')
                    author = author_elem.text.strip() if author_elem else "Anonymous"
                    date_elem = card.find('time')
                    date_str = date_elem.get('datetime') if date_elem else None

                    # Extract pros/cons if available
                    pros_elem = card.find('div', {'data-testid': 'pros'})
                    cons_elem = card.find('div', {'data-testid': 'cons'})
                    pros = pros_elem.text.strip() if pros_elem else ""
                    cons = cons_elem.text.strip() if cons_elem else ""

                    reviews.append({
                        "rating": stars,
                        "title": title,
                        "text": text,
                        "pros": pros,
                        "cons": cons,
                        "author": author,
                        "date": date_str,
                        "platform": "G2"
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse G2 review card: {e}")
                    continue

            self._save_reviews(reviews)

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
            return {"success": False, "error": str(e)}

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

            rating_elem = soup.find('span', class_='overall-rating')
            overall_rating = float(rating_elem.text.strip()) if rating_elem else 0.0

            count_elem = soup.find('span', class_='review-count')
            review_count = 0
            if count_elem:
                match = re.search(r'(\d+)', count_elem.text)
                if match:
                    review_count = int(match.group(1))

            reviews = []
            review_cards = soup.find_all('div', class_='review-card')[:10]

            for card in review_cards:
                try:
                    rating_elem = card.find('div', class_='rating-value')
                    rating = float(rating_elem.text.strip()) if rating_elem else 0.0
                    title_elem = card.find('h3', class_='review-title')
                    title = title_elem.text.strip() if title_elem else ""
                    text_elem = card.find('p', class_='review-text')
                    text = text_elem.text.strip() if text_elem else ""
                    author_elem = card.find('span', class_='reviewer-name')
                    author = author_elem.text.strip() if author_elem else "Anonymous"
                    date_elem = card.find('span', class_='review-date')
                    date_str = date_elem.text.strip() if date_elem else None

                    # Extract pros/cons
                    pros_elem = card.find('div', class_='pros')
                    cons_elem = card.find('div', class_='cons')
                    pros = pros_elem.text.strip() if pros_elem else ""
                    cons = cons_elem.text.strip() if cons_elem else ""

                    reviews.append({
                        "rating": int(rating),
                        "title": title,
                        "text": text,
                        "pros": pros,
                        "cons": cons,
                        "author": author,
                        "date": date_str,
                        "platform": "Capterra"
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse Capterra review card: {e}")
                    continue

            self._save_reviews(reviews)

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
            return {"success": False, "error": str(e)}

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

            rating_elem = soup.find('p', class_='typography_body-l__KUYFJ')
            overall_rating = 0.0
            if rating_elem:
                match = re.search(r'(\d+\.?\d*)', rating_elem.text)
                if match:
                    overall_rating = float(match.group(1))

            count_elem = soup.find('p', string=re.compile(r'\d+ reviews'))
            review_count = 0
            if count_elem:
                match = re.search(r'(\d+)', count_elem.text)
                if match:
                    review_count = int(match.group(1).replace(',', ''))

            reviews = []
            review_cards = soup.find_all('article', class_='review')[:10]

            for card in review_cards:
                try:
                    stars = len(card.find_all('img', alt=re.compile(r'Rated \d')))
                    title_elem = card.find('h2', class_='typography_heading-s')
                    title = title_elem.text.strip() if title_elem else ""
                    text_elem = card.find('p', class_='typography_body-l')
                    text = text_elem.text.strip() if text_elem else ""
                    author_elem = card.find('span', class_='typography_heading-xxs')
                    author = author_elem.text.strip() if author_elem else "Anonymous"
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

            self._save_reviews(reviews)

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
            return {"success": False, "error": str(e)}

    async def scrape_trustradius_reviews(self, product_slug: str) -> Dict[str, Any]:
        """
        Scrape reviews from TrustRadius

        Args:
            product_slug: TrustRadius product slug (e.g., "successifier")
        """
        if not await rate_limit("trustradius_scraping"):
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            url = f"https://www.trustradius.com/products/{product_slug}/reviews"
            response = await self.http_client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract overall TrustRadius score (trScore out of 10)
            score_elem = soup.find('span', class_='trscore')
            overall_rating = 0.0
            if score_elem:
                match = re.search(r'(\d+\.?\d*)', score_elem.text)
                if match:
                    overall_rating = float(match.group(1))

            # Extract review count
            count_elem = soup.find('span', string=re.compile(r'\d+ reviews?', re.I))
            review_count = 0
            if count_elem:
                match = re.search(r'(\d+)', count_elem.text)
                if match:
                    review_count = int(match.group(1))

            reviews = []
            review_cards = soup.find_all('div', class_='review-card')[:10]
            if not review_cards:
                review_cards = soup.find_all('div', attrs={'data-testid': re.compile(r'review')})[:10]

            for card in review_cards:
                try:
                    # TrustRadius uses scores out of 10
                    score_elem = card.find('span', class_='rating-score')
                    score = 0
                    if score_elem:
                        match = re.search(r'(\d+\.?\d*)', score_elem.text)
                        if match:
                            score = round(float(match.group(1)) / 2)  # Convert 10-scale to 5-star

                    title_elem = card.find('h3') or card.find('h2')
                    title = title_elem.text.strip() if title_elem else ""

                    text_elem = card.find('div', class_='review-text') or card.find('p', class_='review-body')
                    text = text_elem.text.strip() if text_elem else ""

                    author_elem = card.find('span', class_='reviewer-name') or card.find('a', class_='reviewer')
                    author = author_elem.text.strip() if author_elem else "Anonymous"

                    # Extract pros/cons (TrustRadius has structured pros/cons)
                    pros_section = card.find('div', class_='pros') or card.find('div', string=re.compile(r'Pros', re.I))
                    cons_section = card.find('div', class_='cons') or card.find('div', string=re.compile(r'Cons', re.I))
                    pros = pros_section.text.strip() if pros_section else ""
                    cons = cons_section.text.strip() if cons_section else ""

                    # Extract company info (TrustRadius often shows company size)
                    company_elem = card.find('span', class_='company-name')
                    company_size_elem = card.find('span', class_='company-size')
                    reviewer_company = company_elem.text.strip() if company_elem else ""
                    reviewer_company_size = company_size_elem.text.strip() if company_size_elem else ""

                    date_elem = card.find('time') or card.find('span', class_='review-date')
                    date_str = date_elem.get('datetime', date_elem.text.strip()) if date_elem else None

                    reviews.append({
                        "rating": score,
                        "title": title,
                        "text": text,
                        "pros": pros,
                        "cons": cons,
                        "author": author,
                        "reviewer_company": reviewer_company,
                        "reviewer_company_size": reviewer_company_size,
                        "date": date_str,
                        "platform": "TrustRadius"
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse TrustRadius review card: {e}")
                    continue

            self._save_reviews(reviews)

            return {
                "success": True,
                "platform": "TrustRadius",
                "overall_rating": overall_rating,
                "review_count": review_count,
                "reviews_scraped": len(reviews),
                "reviews": reviews
            }

        except Exception as e:
            logger.error(f"Failed to scrape TrustRadius reviews: {e}")
            return {"success": False, "error": str(e)}

    async def scrape_software_advice_reviews(self, product_slug: str) -> Dict[str, Any]:
        """
        Scrape reviews from Software Advice

        Args:
            product_slug: Software Advice product slug
        """
        if not await rate_limit("software_advice_scraping"):
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            url = f"https://www.softwareadvice.com/{product_slug}/reviews/"
            response = await self.http_client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract overall rating
            rating_elem = soup.find('span', class_='average-rating') or soup.find('div', class_='overall-score')
            overall_rating = 0.0
            if rating_elem:
                match = re.search(r'(\d+\.?\d*)', rating_elem.text)
                if match:
                    overall_rating = float(match.group(1))

            # Extract review count
            count_elem = soup.find('span', string=re.compile(r'\d+ reviews?', re.I))
            review_count = 0
            if count_elem:
                match = re.search(r'(\d+)', count_elem.text)
                if match:
                    review_count = int(match.group(1))

            reviews = []
            review_cards = soup.find_all('div', class_='review-card')[:10]
            if not review_cards:
                review_cards = soup.find_all('div', class_='review-listing')[:10]

            for card in review_cards:
                try:
                    rating_elem = card.find('span', class_='star-rating') or card.find('div', class_='rating')
                    rating = 0
                    if rating_elem:
                        match = re.search(r'(\d+\.?\d*)', rating_elem.text)
                        if match:
                            rating = round(float(match.group(1)))

                    title_elem = card.find('h3') or card.find('h4')
                    title = title_elem.text.strip() if title_elem else ""

                    text_elem = card.find('div', class_='review-content') or card.find('p', class_='comment')
                    text = text_elem.text.strip() if text_elem else ""

                    author_elem = card.find('span', class_='reviewer') or card.find('div', class_='reviewer-info')
                    author = author_elem.text.strip() if author_elem else "Anonymous"

                    # Extract pros/cons
                    pros_elem = card.find('div', class_='pros')
                    cons_elem = card.find('div', class_='cons')
                    pros = pros_elem.text.strip() if pros_elem else ""
                    cons = cons_elem.text.strip() if cons_elem else ""

                    # Extract industry info
                    industry_elem = card.find('span', class_='industry')
                    reviewer_industry = industry_elem.text.strip() if industry_elem else ""

                    date_elem = card.find('time') or card.find('span', class_='date')
                    date_str = date_elem.get('datetime', date_elem.text.strip()) if date_elem else None

                    reviews.append({
                        "rating": rating,
                        "title": title,
                        "text": text,
                        "pros": pros,
                        "cons": cons,
                        "author": author,
                        "reviewer_industry": reviewer_industry,
                        "date": date_str,
                        "platform": "SoftwareAdvice"
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse Software Advice review card: {e}")
                    continue

            self._save_reviews(reviews)

            return {
                "success": True,
                "platform": "SoftwareAdvice",
                "overall_rating": overall_rating,
                "review_count": review_count,
                "reviews_scraped": len(reviews),
                "reviews": reviews
            }

        except Exception as e:
            logger.error(f"Failed to scrape Software Advice reviews: {e}")
            return {"success": False, "error": str(e)}

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

        # TrustRadius
        trustradius_result = await self.scrape_trustradius_reviews("successifier")
        results["trustradius"] = trustradius_result

        # Software Advice
        software_advice_result = await self.scrape_software_advice_reviews("successifier")
        results["software_advice"] = software_advice_result

        # Calculate aggregate stats
        successful = {k: v for k, v in results.items() if v.get("success")}
        total_reviews = sum(r.get("reviews_scraped", 0) for r in successful.values())

        total_weighted = sum(
            r.get("overall_rating", 0) * r.get("review_count", 0)
            for r in successful.values()
        )
        total_count = sum(r.get("review_count", 1) for r in successful.values())
        avg_rating = total_weighted / total_count if total_count > 0 else 0

        return {
            "total_reviews_scraped": total_reviews,
            "average_rating": round(avg_rating, 2),
            "platforms_scraped": len(successful),
            "platforms_failed": len(results) - len(successful),
            "platforms": results,
            "timestamp": datetime.utcnow().isoformat()
        }


# Global instance
review_scraper = ReviewScraper()
