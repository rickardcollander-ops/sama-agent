"""
SERP Analysis for SEO Agent
Analyzes top search results for target keywords using ValueSERP API
"""

from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import logging

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)

VALUESERP_API = "https://api.valueserp.com/search"


class SERPAnalyzer:
    """Analyze Search Engine Results Pages via ValueSERP API"""

    def __init__(self):
        self.http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        self.sb = None

    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb

    def _is_configured(self) -> bool:
        return bool(settings.VALUESERP_API_KEY)

    async def analyze_serp(self, keyword: str, num_results: int = 5) -> Dict[str, Any]:
        """
        Fetch real Google results via ValueSERP, then analyze each page.
        Falls back to a descriptive error if API key is not configured.
        """
        if not self._is_configured():
            return {
                "success": False,
                "error": "VALUESERP_API_KEY not configured. Get a free key at valueserp.com"
            }

        try:
            search_results = await self._fetch_search_results(keyword, num_results)

            if not search_results:
                return {"success": False, "error": "No search results returned from ValueSERP"}

            # Analyze each result page
            analyzed_results = []
            for i, result in enumerate(search_results, 1):
                analysis = await self._analyze_page(result["url"])
                if analysis:
                    analyzed_results.append({
                        "position": i,
                        "url": result["url"],
                        "title": result.get("title", ""),
                        "snippet": result.get("snippet", ""),
                        **analysis
                    })

            insights = self._generate_insights(analyzed_results, keyword)

            # Persist to Supabase
            try:
                sb = self._get_sb()
                sb.table("serp_analysis").insert({
                    "keyword": keyword,
                    "results": analyzed_results,
                    "insights": insights,
                    "analyzed_at": datetime.utcnow().isoformat()
                }).execute()
            except Exception as db_err:
                logger.warning(f"Could not save SERP analysis to DB: {db_err}")

            return {
                "success": True,
                "keyword": keyword,
                "results_analyzed": len(analyzed_results),
                "results": analyzed_results,
                "insights": insights
            }

        except Exception as e:
            logger.error(f"SERP analysis failed: {e}")
            return {"success": False, "error": str(e)}

    async def _fetch_search_results(self, keyword: str, num_results: int) -> List[Dict[str, str]]:
        """Fetch real Google search results via ValueSERP API"""
        params = {
            "api_key": settings.VALUESERP_API_KEY,
            "q": keyword,
            "num": min(num_results, 10),
            "gl": "us",
            "hl": "en",
            "google_domain": "google.com"
        }

        resp = await self.http_client.get(VALUESERP_API, params=params)

        if resp.status_code != 200:
            logger.warning(f"ValueSERP API returned {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        organic = data.get("organic_results", [])

        return [
            {
                "url": r.get("link", ""),
                "title": r.get("title", ""),
                "snippet": r.get("snippet", "")
            }
            for r in organic
            if r.get("link")
        ][:num_results]

    async def _analyze_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch and analyze a single SERP result page"""
        try:
            response = await self.http_client.get(url, follow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove script/style noise
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            word_count = len(soup.get_text(separator=" ").split())

            h1_tags = [h.get_text().strip() for h in soup.find_all("h1")]
            h2_tags = [h.get_text().strip() for h in soup.find_all("h2")]
            h3_tags = [h.get_text().strip() for h in soup.find_all("h3")]

            title_tag = soup.find("title")
            title_text = title_tag.get_text().strip() if title_tag else ""

            meta_desc = soup.find("meta", attrs={"name": "description"})
            meta_description = meta_desc.get("content", "") if meta_desc else ""

            images = soup.find_all("img")
            image_count = len(images)
            images_with_alt = len([img for img in images if img.get("alt")])

            domain = url.split("/")[2] if "/" in url else url
            internal_links = len([
                a for a in soup.find_all("a", href=True)
                if domain in a["href"]
            ])
            external_links = len([
                a for a in soup.find_all("a", href=True)
                if domain not in a["href"] and a["href"].startswith("http")
            ])

            schema_scripts = soup.find_all("script", type="application/ld+json")
            has_schema = len(schema_scripts) > 0

            return {
                "word_count": word_count,
                "page_title": title_text,
                "title_length": len(title_text),
                "meta_description": meta_description,
                "meta_description_length": len(meta_description),
                "h1_count": len(h1_tags),
                "h2_count": len(h2_tags),
                "h3_count": len(h3_tags),
                "h1_tags": h1_tags[:3],
                "h2_tags": h2_tags[:5],
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
        try:
            import json
            data = json.loads(script_tag.string)
            return data.get("@type", "Unknown")
        except Exception:
            return "Unknown"

    def _generate_insights(self, results: List[Dict[str, Any]], keyword: str) -> Dict[str, Any]:
        if not results:
            return {}

        avg_word_count = sum(r.get("word_count", 0) for r in results) / len(results)
        avg_h2_count = sum(r.get("h2_count", 0) for r in results) / len(results)
        avg_images = sum(r.get("image_count", 0) for r in results) / len(results)
        schema_usage = sum(1 for r in results if r.get("has_schema_markup", False))

        all_h2s = []
        for r in results:
            all_h2s.extend(r.get("h2_tags", []))

        return {
            "recommended_word_count": int(avg_word_count),
            "recommended_h2_count": int(avg_h2_count),
            "recommended_images": int(avg_images),
            "schema_usage_percentage": round((schema_usage / len(results)) * 100, 0),
            "common_topics": list(dict.fromkeys(all_h2s))[:10],
            "competitive_analysis": {
                "avg_word_count": int(avg_word_count),
                "min_word_count": min(r.get("word_count", 0) for r in results),
                "max_word_count": max(r.get("word_count", 0) for r in results),
                "schema_adoption": f"{schema_usage}/{len(results)} pages"
            },
            "recommendations": self._generate_recommendations(results, keyword)
        }

    def _generate_recommendations(self, results: List[Dict[str, Any]], keyword: str) -> List[str]:
        recommendations = []

        avg_word_count = sum(r.get("word_count", 0) for r in results) / len(results)
        schema_usage = sum(1 for r in results if r.get("has_schema_markup", False))
        avg_h2 = sum(r.get("h2_count", 0) for r in results) / len(results)
        avg_images = sum(r.get("image_count", 0) for r in results) / len(results)

        recommendations.append(f"Target {int(avg_word_count)} words to match top results")

        if schema_usage > len(results) / 2:
            recommendations.append("Add schema markup — majority of top results use it")

        recommendations.append(f"Use ~{int(avg_h2)} H2 headings for optimal structure")
        recommendations.append(f"Include ~{int(avg_images)} images with descriptive alt text")
        recommendations.append(f"Include '{keyword}' in title tag and H1")

        return recommendations


# Global instance
serp_analyzer = SERPAnalyzer()
