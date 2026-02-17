"""
Google Indexing API Integration
Notifies Google when new content is published or updated
"""

from typing import Dict, Any, List, Optional
import httpx
import logging
from datetime import datetime

from shared.config import settings
from shared.rate_limiter import rate_limit
from shared.database import get_supabase

logger = logging.getLogger(__name__)


class GoogleIndexingAPI:
    """Google Indexing API client"""
    
    def __init__(self):
        self.base_url = "https://indexing.googleapis.com/v3"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def _get_access_token(self) -> str:
        """Get OAuth2 access token for Google Indexing API"""
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
    
    async def notify_url_updated(self, url: str) -> Dict[str, Any]:
        """
        Notify Google that a URL has been updated
        
        Args:
            url: Full URL of the updated page
        """
        if not await rate_limit("google_indexing_api"):
            return {
                "success": False,
                "error": "Rate limit exceeded for Google Indexing API"
            }
        
        try:
            access_token = await self._get_access_token()
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "url": url,
                "type": "URL_UPDATED"
            }
            
            response = await self.http_client.post(
                f"{self.base_url}/urlNotifications:publish",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Log to database
                sb = self._get_sb()
                sb.table("indexing_requests").insert({
                    "url": url,
                    "type": "URL_UPDATED",
                    "status": "success",
                    "response": result,
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
                
                logger.info(f"Successfully notified Google about URL update: {url}")
                return {
                    "success": True,
                    "url": url,
                    "response": result
                }
            else:
                error_msg = response.text
                logger.error(f"Failed to notify Google: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg
                }
                
        except Exception as e:
            logger.error(f"Error notifying Google Indexing API: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def notify_url_deleted(self, url: str) -> Dict[str, Any]:
        """
        Notify Google that a URL has been deleted
        
        Args:
            url: Full URL of the deleted page
        """
        if not await rate_limit("google_indexing_api"):
            return {
                "success": False,
                "error": "Rate limit exceeded"
            }
        
        try:
            access_token = await self._get_access_token()
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "url": url,
                "type": "URL_DELETED"
            }
            
            response = await self.http_client.post(
                f"{self.base_url}/urlNotifications:publish",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                logger.info(f"Successfully notified Google about URL deletion: {url}")
                return {
                    "success": True,
                    "url": url
                }
            else:
                return {
                    "success": False,
                    "error": response.text
                }
                
        except Exception as e:
            logger.error(f"Error notifying Google Indexing API: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_notification_metadata(self, url: str) -> Dict[str, Any]:
        """
        Get metadata about previous indexing notifications for a URL
        
        Args:
            url: Full URL to check
        """
        try:
            access_token = await self._get_access_token()
            
            headers = {
                "Authorization": f"Bearer {access_token}"
            }
            
            response = await self.http_client.get(
                f"{self.base_url}/urlNotifications/metadata",
                headers=headers,
                params={"url": url}
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "metadata": response.json()
                }
            else:
                return {
                    "success": False,
                    "error": response.text
                }
                
        except Exception as e:
            logger.error(f"Error getting notification metadata: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def batch_notify_urls(self, urls: List[str], notification_type: str = "URL_UPDATED") -> Dict[str, Any]:
        """
        Batch notify multiple URLs
        
        Args:
            urls: List of URLs to notify
            notification_type: "URL_UPDATED" or "URL_DELETED"
        """
        results = {
            "success": [],
            "failed": []
        }
        
        for url in urls:
            if notification_type == "URL_UPDATED":
                result = await self.notify_url_updated(url)
            else:
                result = await self.notify_url_deleted(url)
            
            if result.get("success"):
                results["success"].append(url)
            else:
                results["failed"].append({
                    "url": url,
                    "error": result.get("error")
                })
        
        return {
            "total": len(urls),
            "successful": len(results["success"]),
            "failed": len(results["failed"]),
            "results": results
        }


# Global instance
indexing_api = GoogleIndexingAPI()
