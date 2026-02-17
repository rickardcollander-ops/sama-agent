"""
Content Analytics - GA4 Integration
Tracks content performance using Google Analytics 4 API
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import httpx
import logging

from shared.config import settings
from shared.database import get_supabase
from shared.rate_limiter import rate_limit

logger = logging.getLogger(__name__)


class ContentAnalytics:
    """Track content performance via GA4 API"""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.property_id = "properties/YOUR_GA4_PROPERTY_ID"  # TODO: Add to settings
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def _get_access_token(self) -> str:
        """Get OAuth2 access token for GA4 API"""
        token_url = "https://oauth2.googleapis.com/token"
        
        data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": settings.GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token"
        }
        
        response = await self.http_client.post(token_url, data=data)
        response.raise_for_status()
        
        return response.json()["access_token"]
    
    async def get_content_performance(
        self,
        url_path: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get performance metrics for a specific content piece
        
        Args:
            url_path: URL path (e.g., "/blog/reduce-churn")
            days: Number of days to analyze
        
        Returns:
            Performance metrics including pageviews, time on page, bounce rate
        """
        if not await rate_limit("google_analytics_api"):
            return {"error": "Rate limit exceeded"}
        
        try:
            access_token = await self._get_access_token()
            
            # Calculate date range
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            # GA4 Data API request
            url = f"https://analyticsdata.googleapis.com/v1beta/{self.property_id}:runReport"
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "dateRanges": [{
                    "startDate": start_date.strftime("%Y-%m-%d"),
                    "endDate": end_date.strftime("%Y-%m-%d")
                }],
                "dimensions": [
                    {"name": "pagePath"}
                ],
                "metrics": [
                    {"name": "screenPageViews"},
                    {"name": "averageSessionDuration"},
                    {"name": "bounceRate"},
                    {"name": "engagementRate"},
                    {"name": "conversions"}
                ],
                "dimensionFilter": {
                    "filter": {
                        "fieldName": "pagePath",
                        "stringFilter": {
                            "matchType": "EXACT",
                            "value": url_path
                        }
                    }
                }
            }
            
            response = await self.http_client.post(url, headers=headers, json=payload)
            
            if response.status_code != 200:
                logger.error(f"GA4 API error: {response.text}")
                return {"error": response.text}
            
            data = response.json()
            
            # Parse response
            if not data.get("rows"):
                return {
                    "url_path": url_path,
                    "pageviews": 0,
                    "avg_time_on_page": 0,
                    "bounce_rate": 0,
                    "engagement_rate": 0,
                    "conversions": 0,
                    "period_days": days
                }
            
            row = data["rows"][0]
            metrics = row["metricValues"]
            
            result = {
                "url_path": url_path,
                "pageviews": int(metrics[0]["value"]),
                "avg_time_on_page": float(metrics[1]["value"]),
                "bounce_rate": float(metrics[2]["value"]) * 100,
                "engagement_rate": float(metrics[3]["value"]) * 100,
                "conversions": int(metrics[4]["value"]),
                "period_days": days,
                "analyzed_at": datetime.utcnow().isoformat()
            }
            
            # Save to database
            sb = self._get_sb()
            sb.table("content_performance").insert(result).execute()
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get content performance: {e}")
            return {"error": str(e)}
    
    async def get_top_performing_content(
        self,
        days: int = 30,
        limit: int = 10,
        metric: str = "pageviews"
    ) -> List[Dict[str, Any]]:
        """
        Get top performing content pieces
        
        Args:
            days: Number of days to analyze
            limit: Number of results to return
            metric: Metric to sort by (pageviews, engagement_rate, conversions)
        
        Returns:
            List of top performing content
        """
        try:
            access_token = await self._get_access_token()
            
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            url = f"https://analyticsdata.googleapis.com/v1beta/{self.property_id}:runReport"
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Map metric names
            metric_map = {
                "pageviews": "screenPageViews",
                "engagement_rate": "engagementRate",
                "conversions": "conversions",
                "time_on_page": "averageSessionDuration"
            }
            
            payload = {
                "dateRanges": [{
                    "startDate": start_date.strftime("%Y-%m-%d"),
                    "endDate": end_date.strftime("%Y-%m-%d")
                }],
                "dimensions": [
                    {"name": "pagePath"},
                    {"name": "pageTitle"}
                ],
                "metrics": [
                    {"name": "screenPageViews"},
                    {"name": "averageSessionDuration"},
                    {"name": "bounceRate"},
                    {"name": "engagementRate"},
                    {"name": "conversions"}
                ],
                "orderBys": [{
                    "metric": {
                        "metricName": metric_map.get(metric, "screenPageViews")
                    },
                    "desc": True
                }],
                "limit": limit,
                "dimensionFilter": {
                    "filter": {
                        "fieldName": "pagePath",
                        "stringFilter": {
                            "matchType": "BEGINS_WITH",
                            "value": "/blog/"
                        }
                    }
                }
            }
            
            response = await self.http_client.post(url, headers=headers, json=payload)
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            
            results = []
            for row in data.get("rows", []):
                dimensions = row["dimensionValues"]
                metrics = row["metricValues"]
                
                results.append({
                    "url_path": dimensions[0]["value"],
                    "title": dimensions[1]["value"],
                    "pageviews": int(metrics[0]["value"]),
                    "avg_time_on_page": float(metrics[1]["value"]),
                    "bounce_rate": float(metrics[2]["value"]) * 100,
                    "engagement_rate": float(metrics[3]["value"]) * 100,
                    "conversions": int(metrics[4]["value"])
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get top performing content: {e}")
            return []
    
    async def track_content_piece(self, content_id: str, url_path: str) -> Dict[str, Any]:
        """
        Track a specific content piece and update database
        
        Args:
            content_id: Content ID in database
            url_path: URL path of the content
        """
        try:
            # Get performance data
            performance = await self.get_content_performance(url_path, days=30)
            
            if "error" in performance:
                return performance
            
            # Update content_library with performance data
            sb = self._get_sb()
            sb.table("content_library")\
                .update({
                    "pageviews_30d": performance["pageviews"],
                    "avg_time_on_page": performance["avg_time_on_page"],
                    "bounce_rate": performance["bounce_rate"],
                    "engagement_rate": performance["engagement_rate"],
                    "conversions_30d": performance["conversions"],
                    "last_analytics_update": datetime.utcnow().isoformat()
                })\
                .eq("id", content_id)\
                .execute()
            
            return {
                "success": True,
                "content_id": content_id,
                **performance
            }
            
        except Exception as e:
            logger.error(f"Failed to track content piece: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def identify_underperforming_content(
        self,
        threshold_pageviews: int = 100,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Identify content that's underperforming
        
        Args:
            threshold_pageviews: Minimum pageviews threshold
            days: Period to analyze
        
        Returns:
            List of underperforming content pieces
        """
        try:
            sb = self._get_sb()
            
            # Get all published content
            result = sb.table("content_library")\
                .select("*")\
                .eq("status", "published")\
                .execute()
            
            content = result.data if result.data else []
            
            underperforming = []
            
            for piece in content:
                if piece.get("url"):
                    performance = await self.get_content_performance(piece["url"], days)
                    
                    if not "error" in performance and performance["pageviews"] < threshold_pageviews:
                        underperforming.append({
                            "content_id": piece["id"],
                            "title": piece["title"],
                            "url": piece["url"],
                            "pageviews": performance["pageviews"],
                            "published_date": piece.get("created_at"),
                            "recommendation": "Consider refreshing or promoting this content"
                        })
            
            return underperforming
            
        except Exception as e:
            logger.error(f"Failed to identify underperforming content: {e}")
            return []


# Global instance
content_analytics = ContentAnalytics()
