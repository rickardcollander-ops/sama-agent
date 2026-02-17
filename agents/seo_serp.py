"""
SERP Analysis for SEO Agent
Analyzes top 5 search results for target keywords
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


class SERPAnalyzer:
    """Analyze Search Engine Results Pages"""
    
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
    
    async def analyze_serp(self, keyword: str, num_results: int = 5) -> Dict[str, Any]:
        """
        Analyze top search results for a keyword
        
        Args:
            keyword: Target keyword to analyze
            num_results: Number of results to analyze (default 5)
        
        Returns:
            Analysis of top results including content length, headings, meta, etc.
        """
        try:
            # Get search results
            search_results = await self._fetch_search_results(keyword, num_results)
            
            if not search_results:
                return {
                    "success": False,
                    "error": "No search results found"
                }
            
            # Analyze each result
            analyzed_results = []
            for i, result in enumerate(search_results, 1):
                analysis = await self._analyze_page(result["url"], i)
                if analysis:
                    analyzed_results.append({
                        "position": i,
                        "url": result["url"],
                        "title": result["title"],
                        **analysis
                    })
            
            # Generate insights
            insights = self._generate_insights(analyzed_results, keyword)
            
            # Save to database
            sb = self._get_sb()
            sb.table("serp_analysis").insert({
                "keyword": keyword,
                "results": analyzed_results,
                "insights": insights,
                "analyzed_at": datetime.utcnow().isoformat()
            }).execute()
            
            return {
                "success": True,
                "keyword": keyword,
                "results_analyzed": len(analyzed_results),
                "results": analyzed_results,
                "insights": insights
            }
            
        except Exception as e:
            logger.error(f"SERP analysis failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _fetch_search_results(self, keyword: str, num_results: int) -> List[Dict[str, str]]:
        """Fetch search results from Google"""
        # Note: In production, use Google Custom Search API
        # For now, return mock data structure
        
        # This would normally call Google Custom Search API
        # For MVP, we'll return structure that can be populated
        
        return [
            {
                "url": f"https://example.com/result-{i}",
                "title": f"Result {i} for {keyword}"
            }
            for i in range(1, num_results + 1)
        ]
    
    async def _analyze_page(self, url: str, position: int) -> Optional[Dict[str, Any]]:
        """Analyze a single page"""
        try:
            if not await rate_limit("serp_analysis"):
                return None
            
            response = await self.http_client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract content metrics
            word_count = len(soup.get_text().split())
            
            # Extract headings
            h1_tags = [h.get_text().strip() for h in soup.find_all('h1')]
            h2_tags = [h.get_text().strip() for h in soup.find_all('h2')]
            h3_tags = [h.get_text().strip() for h in soup.find_all('h3')]
            
            # Extract meta tags
            title = soup.find('title')
            title_text = title.get_text().strip() if title else ""
            
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            meta_description = meta_desc.get('content', '') if meta_desc else ""
            
            # Extract images
            images = soup.find_all('img')
            image_count = len(images)
            images_with_alt = len([img for img in images if img.get('alt')])
            
            # Extract links
            internal_links = len([a for a in soup.find_all('a', href=True) if url in a['href']])
            external_links = len([a for a in soup.find_all('a', href=True) if url not in a['href']])
            
            # Check for schema markup
            schema_scripts = soup.find_all('script', type='application/ld+json')
            has_schema = len(schema_scripts) > 0
            
            return {
                "word_count": word_count,
                "title": title_text,
                "title_length": len(title_text),
                "meta_description": meta_description,
                "meta_description_length": len(meta_description),
                "h1_count": len(h1_tags),
                "h2_count": len(h2_tags),
                "h3_count": len(h3_tags),
                "h1_tags": h1_tags[:3],  # First 3
                "h2_tags": h2_tags[:5],  # First 5
                "image_count": image_count,
                "images_with_alt": images_with_alt,
                "internal_links": internal_links,
                "external_links": external_links,
                "has_schema_markup": has_schema,
                "schema_types": [self._extract_schema_type(s) for s in schema_scripts]
            }
            
        except Exception as e:
            logger.warning(f"Failed to analyze {url}: {e}")
            return None
    
    def _extract_schema_type(self, script_tag) -> str:
        """Extract schema type from script tag"""
        try:
            import json
            data = json.loads(script_tag.string)
            return data.get('@type', 'Unknown')
        except:
            return 'Unknown'
    
    def _generate_insights(self, results: List[Dict[str, Any]], keyword: str) -> Dict[str, Any]:
        """Generate insights from analyzed results"""
        if not results:
            return {}
        
        # Calculate averages
        avg_word_count = sum(r.get("word_count", 0) for r in results) / len(results)
        avg_h2_count = sum(r.get("h2_count", 0) for r in results) / len(results)
        avg_images = sum(r.get("image_count", 0) for r in results) / len(results)
        
        # Schema usage
        schema_usage = sum(1 for r in results if r.get("has_schema_markup", False))
        
        # Common H2 topics
        all_h2s = []
        for r in results:
            all_h2s.extend(r.get("h2_tags", []))
        
        return {
            "recommended_word_count": int(avg_word_count),
            "recommended_h2_count": int(avg_h2_count),
            "recommended_images": int(avg_images),
            "schema_usage_percentage": (schema_usage / len(results)) * 100,
            "common_topics": list(set(all_h2s))[:10],  # Top 10 unique topics
            "competitive_analysis": {
                "avg_word_count": int(avg_word_count),
                "min_word_count": min(r.get("word_count", 0) for r in results),
                "max_word_count": max(r.get("word_count", 0) for r in results),
                "schema_adoption": f"{schema_usage}/{len(results)} pages"
            },
            "recommendations": self._generate_recommendations(results, keyword)
        }
    
    def _generate_recommendations(self, results: List[Dict[str, Any]], keyword: str) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        avg_word_count = sum(r.get("word_count", 0) for r in results) / len(results)
        schema_usage = sum(1 for r in results if r.get("has_schema_markup", False))
        
        # Word count recommendation
        recommendations.append(f"Target {int(avg_word_count)} words to match top results")
        
        # Schema recommendation
        if schema_usage > len(results) / 2:
            recommendations.append("Add schema markup - majority of top results use it")
        
        # H2 structure
        avg_h2 = sum(r.get("h2_count", 0) for r in results) / len(results)
        recommendations.append(f"Use {int(avg_h2)} H2 headings for optimal structure")
        
        # Images
        avg_images = sum(r.get("image_count", 0) for r in results) / len(results)
        recommendations.append(f"Include {int(avg_images)} images with descriptive alt text")
        
        # Keyword in title
        recommendations.append(f"Include '{keyword}' in title tag and H1")
        
        return recommendations


# Global instance
serp_analyzer = SERPAnalyzer()
