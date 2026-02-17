"""
LinkedIn Integration for Social Agent
Handles LinkedIn posting, engagement, and analytics
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import httpx
import logging

from shared.config import settings
from shared.database import get_supabase
from shared.rate_limiter import rate_limit
from agents.brand_voice import brand_voice

logger = logging.getLogger(__name__)


class LinkedInManager:
    """Manage LinkedIn posts and engagement"""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.access_token = settings.LINKEDIN_ACCESS_TOKEN if hasattr(settings, 'LINKEDIN_ACCESS_TOKEN') else None
        self.organization_id = settings.LINKEDIN_ORG_ID if hasattr(settings, 'LINKEDIN_ORG_ID') else None
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def create_post(
        self,
        content: str,
        link_url: Optional[str] = None,
        image_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a LinkedIn post
        
        Args:
            content: Post text content
            link_url: Optional URL to share
            image_url: Optional image URL
        
        Returns:
            Post creation result with post ID
        """
        if not self.access_token:
            return {"error": "LinkedIn access token not configured"}
        
        if not await rate_limit("linkedin_api"):
            return {"error": "Rate limit exceeded"}
        
        try:
            url = "https://api.linkedin.com/v2/ugcPosts"
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0"
            }
            
            # Build post payload
            payload = {
                "author": f"urn:li:organization:{self.organization_id}",
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": content
                        },
                        "shareMediaCategory": "NONE"
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }
            
            # Add link if provided
            if link_url:
                payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "ARTICLE"
                payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
                    "status": "READY",
                    "originalUrl": link_url
                }]
            
            # Add image if provided
            if image_url and not link_url:
                payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "IMAGE"
                payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
                    "status": "READY",
                    "media": image_url
                }]
            
            response = await self.http_client.post(url, headers=headers, json=payload)
            
            if response.status_code == 201:
                result = response.json()
                post_id = result.get("id")
                
                # Save to database
                sb = self._get_sb()
                sb.table("social_posts").insert({
                    "platform": "linkedin",
                    "content": content,
                    "link_url": link_url,
                    "image_url": image_url,
                    "post_id": post_id,
                    "status": "published",
                    "published_at": datetime.utcnow().isoformat()
                }).execute()
                
                logger.info(f"LinkedIn post created: {post_id}")
                
                return {
                    "success": True,
                    "post_id": post_id,
                    "platform": "linkedin",
                    "content": content
                }
            else:
                logger.error(f"LinkedIn API error: {response.text}")
                return {
                    "success": False,
                    "error": response.text
                }
                
        except Exception as e:
            logger.error(f"Failed to create LinkedIn post: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_post_analytics(self, post_id: str) -> Dict[str, Any]:
        """
        Get analytics for a LinkedIn post
        
        Args:
            post_id: LinkedIn post ID
        
        Returns:
            Post analytics including impressions, clicks, engagement
        """
        if not self.access_token:
            return {"error": "LinkedIn access token not configured"}
        
        try:
            # Get post statistics
            url = f"https://api.linkedin.com/v2/organizationalEntityShareStatistics"
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "X-Restli-Protocol-Version": "2.0.0"
            }
            
            params = {
                "q": "organizationalEntity",
                "organizationalEntity": f"urn:li:organization:{self.organization_id}",
                "shares": [post_id]
            }
            
            response = await self.http_client.get(url, headers=headers, params=params)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("elements"):
                    stats = data["elements"][0]
                    
                    return {
                        "post_id": post_id,
                        "impressions": stats.get("impressionCount", 0),
                        "clicks": stats.get("clickCount", 0),
                        "likes": stats.get("likeCount", 0),
                        "comments": stats.get("commentCount", 0),
                        "shares": stats.get("shareCount", 0),
                        "engagement_rate": self._calculate_engagement_rate(stats)
                    }
            
            return {"error": "No analytics data available"}
            
        except Exception as e:
            logger.error(f"Failed to get LinkedIn analytics: {e}")
            return {"error": str(e)}
    
    def _calculate_engagement_rate(self, stats: Dict[str, Any]) -> float:
        """Calculate engagement rate from stats"""
        impressions = stats.get("impressionCount", 0)
        if impressions == 0:
            return 0.0
        
        engagements = (
            stats.get("likeCount", 0) +
            stats.get("commentCount", 0) +
            stats.get("shareCount", 0) +
            stats.get("clickCount", 0)
        )
        
        return (engagements / impressions) * 100
    
    async def generate_linkedin_post(
        self,
        topic: str,
        style: str = "professional",
        include_hashtags: bool = True
    ) -> Dict[str, Any]:
        """
        Generate LinkedIn post content using AI
        
        Args:
            topic: Topic to write about
            style: Post style (professional, thought_leadership, educational)
            include_hashtags: Whether to include hashtags
        
        Returns:
            Generated post content
        """
        from anthropic import Anthropic
        
        if not settings.ANTHROPIC_API_KEY:
            return {"error": "Anthropic API key not configured"}
        
        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        
        system_prompt = f"""You are a LinkedIn content expert for Successifier, an AI-native Customer Success platform.

Brand Voice: {brand_voice['tone']}

Generate a LinkedIn post that:
- Is professional and value-driven
- 150-300 words (LinkedIn sweet spot)
- Starts with a hook
- Provides actionable insights
- Ends with a question or CTA
- Uses line breaks for readability
- Style: {style}

Format as JSON:
{{
  "content": "Post text with line breaks",
  "hashtags": ["CustomerSuccess", "SaaS", "ChurnPrevention"],
  "cta": "Call to action"
}}"""
        
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                system=system_prompt,
                messages=[{"role": "user", "content": f"Create LinkedIn post about: {topic}"}]
            )
            
            import json
            result = json.loads(response.content[0].text)
            
            # Add hashtags if requested
            if include_hashtags and result.get("hashtags"):
                hashtag_str = " ".join(f"#{tag}" for tag in result["hashtags"])
                result["content"] = f"{result['content']}\n\n{hashtag_str}"
            
            return {
                "success": True,
                **result
            }
            
        except Exception as e:
            logger.error(f"Failed to generate LinkedIn post: {e}")
            return {"error": str(e)}
    
    async def schedule_post(
        self,
        content: str,
        scheduled_time: datetime,
        link_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Schedule a LinkedIn post for later
        
        Args:
            content: Post content
            scheduled_time: When to publish
            link_url: Optional link
        
        Returns:
            Scheduled post info
        """
        try:
            sb = self._get_sb()
            
            result = sb.table("social_posts").insert({
                "platform": "linkedin",
                "content": content,
                "link_url": link_url,
                "status": "scheduled",
                "scheduled_for": scheduled_time.isoformat(),
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            
            return {
                "success": True,
                "scheduled_post_id": result.data[0]["id"],
                "scheduled_for": scheduled_time.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to schedule LinkedIn post: {e}")
            return {"error": str(e)}
    
    async def get_company_analytics(self, days: int = 30) -> Dict[str, Any]:
        """
        Get overall company page analytics
        
        Args:
            days: Number of days to analyze
        
        Returns:
            Company page analytics
        """
        if not self.access_token:
            return {"error": "LinkedIn access token not configured"}
        
        try:
            url = f"https://api.linkedin.com/v2/organizationPageStatistics/{self.organization_id}"
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "X-Restli-Protocol-Version": "2.0.0"
            }
            
            response = await self.http_client.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                
                return {
                    "follower_count": data.get("followerCount", 0),
                    "page_views": data.get("pageViews", 0),
                    "unique_visitors": data.get("uniqueVisitors", 0),
                    "period_days": days
                }
            
            return {"error": "Analytics not available"}
            
        except Exception as e:
            logger.error(f"Failed to get company analytics: {e}")
            return {"error": str(e)}


# Global instance
linkedin_manager = LinkedInManager()
