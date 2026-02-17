"""
Social Agent - X/Twitter Management and Engagement
Manages social media presence for successifier.com
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)


class SocialAgent:
    """
    Social Agent responsible for:
    - X/Twitter post scheduling
    - Engagement monitoring
    - Reply generation
    - Thread creation
    - Hashtag strategy
    - Competitor monitoring
    """
    
    # Content calendar from SAMA 2.0 spec
    CONTENT_CALENDAR = {
        "monday": {
            "theme": "Churn Prevention Tips",
            "format": "Educational thread",
            "example": "3 churn signals most CS teams miss (and how AI catches them)"
        },
        "tuesday": {
            "theme": "Product Updates",
            "format": "Feature announcement",
            "example": "New: AI-powered health score predictions now live"
        },
        "wednesday": {
            "theme": "Customer Success Best Practices",
            "format": "How-to post",
            "example": "How to build a customer health score that actually predicts churn"
        },
        "thursday": {
            "theme": "Data & Insights",
            "format": "Stat + insight",
            "example": "40% of churn happens in the first 90 days. Here's why..."
        },
        "friday": {
            "theme": "Case Study / Social Proof",
            "format": "Customer story",
            "example": "How [Company] reduced churn by 40% in 6 months"
        }
    }
    
    # Engagement rules from SAMA 2.0 spec
    ENGAGEMENT_RULES = {
        "reply_to_mentions": {
            "condition": "Mentioned by user with >500 followers OR existing customer",
            "action": "Generate personalized reply within 2 hours",
            "tone": "Helpful, professional, not salesy"
        },
        "engage_with_prospects": {
            "condition": "User tweets about churn, CS, or competitor pain points",
            "action": "Like + thoughtful reply if relevant",
            "tone": "Provide value first, mention Successifier only if directly relevant"
        },
        "retweet_customers": {
            "condition": "Customer mentions Successifier positively",
            "action": "Retweet + thank them",
            "tone": "Grateful, authentic"
        },
        "monitor_competitors": {
            "condition": "User complains about Gainsight/Totango/ChurnZero",
            "action": "Like + offer alternative (if appropriate)",
            "tone": "Empathetic, not opportunistic"
        }
    }
    
    # Hashtag strategy
    HASHTAG_STRATEGY = {
        "primary": ["#CustomerSuccess", "#SaaS", "#ChurnPrevention"],
        "secondary": ["#CSLeadership", "#CustomerExperience", "#B2BSaaS"],
        "avoid": ["#AI", "#Tech", "#Startup"]  # Too generic
    }
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
    
    async def generate_post(
        self,
        topic: str,
        style: str = "educational",
        thread: bool = False
    ) -> Dict[str, Any]:
        """
        Generate X/Twitter post
        
        Args:
            topic: Post topic
            style: Post style (educational, announcement, engagement)
            thread: Whether to generate a thread
        
        Returns:
            Generated post(s)
        """
        logger.info(f"🐦 Generating Twitter post: {topic}")
        
        system_prompt = brand_voice.get_system_prompt("blog")
        
        if thread:
            user_prompt = f"""Create a Twitter/X thread about: {topic}

Style: {style}

Requirements:
- 3-5 tweets in a thread
- First tweet: Hook (grab attention)
- Middle tweets: Value/insights
- Last tweet: Takeaway + optional CTA
- Each tweet max 280 characters
- Use emojis sparingly (1-2 per tweet max)
- No hashtags in thread (only in first tweet if needed)
- Professional but conversational tone

Include Successifier proof points where relevant:
- 40% churn reduction
- 25% NRR improvement
- 85% less manual work

Format as JSON array:
["tweet 1", "tweet 2", "tweet 3", ...]
"""
        else:
            user_prompt = f"""Create a single Twitter/X post about: {topic}

Style: {style}

Requirements:
- Max 280 characters
- Engaging and valuable
- Professional but conversational
- 1-2 emojis max
- 1-2 hashtags if relevant (from: #CustomerSuccess, #SaaS, #ChurnPrevention)
- Include data/insight if possible

Format as plain text.
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text.strip()
        
        if thread:
            import json
            try:
                tweets = json.loads(content)
            except:
                # Fallback: split by newlines
                tweets = [t.strip() for t in content.split('\n') if t.strip()]
        else:
            tweets = [content]
        
        # Validate character limits
        tweets = [t[:280] for t in tweets]
        
        logger.info(f"✅ Generated {len(tweets)} tweet(s)")
        
        return {
            "topic": topic,
            "style": style,
            "is_thread": thread,
            "tweets": tweets,
            "status": "draft"
        }
    
    async def generate_reply(
        self,
        original_tweet: str,
        context: Optional[str] = None
    ) -> str:
        """
        Generate reply to a tweet
        
        Args:
            original_tweet: Tweet to reply to
            context: Additional context about the user/situation
        
        Returns:
            Generated reply
        """
        logger.info(f"💬 Generating reply to tweet")
        
        system_prompt = brand_voice.get_system_prompt("blog")
        
        user_prompt = f"""Generate a reply to this tweet:

"{original_tweet}"
"""
        
        if context:
            user_prompt += f"\nContext: {context}"
        
        user_prompt += """

Requirements:
- Max 280 characters
- Helpful and valuable
- Professional but friendly
- Don't be salesy unless they explicitly ask about solutions
- Provide insight or ask a thoughtful question
- Use 1 emoji max

Format as plain text.
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        reply = response.content[0].text.strip()[:280]
        
        logger.info(f"✅ Reply generated")
        
        return reply
    
    async def schedule_posts(
        self,
        date_range: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Generate content calendar for next N days
        
        Args:
            date_range: Number of days to schedule
        
        Returns:
            List of scheduled posts
        """
        logger.info(f"📅 Generating {date_range}-day content calendar")
        
        scheduled_posts = []
        start_date = datetime.now()
        
        for day_offset in range(date_range):
            post_date = start_date + timedelta(days=day_offset)
            day_name = post_date.strftime("%A").lower()
            
            # Get theme for this day
            if day_name in self.CONTENT_CALENDAR:
                day_config = self.CONTENT_CALENDAR[day_name]
                
                # Generate post based on theme
                post = await self.generate_post(
                    topic=day_config["example"],
                    style="educational",
                    thread=day_config["format"] == "Educational thread"
                )
                
                scheduled_posts.append({
                    "date": post_date.strftime("%Y-%m-%d"),
                    "day": day_name,
                    "theme": day_config["theme"],
                    "format": day_config["format"],
                    "post": post,
                    "scheduled_time": "09:00"  # Default to 9 AM
                })
        
        logger.info(f"✅ Generated {len(scheduled_posts)} scheduled posts")
        
        return scheduled_posts
    
    async def monitor_mentions(
        self,
        mentions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Monitor and prioritize mentions for response
        
        Args:
            mentions: List of mentions from Twitter API
        
        Returns:
            Prioritized mentions with suggested actions
        """
        logger.info(f"👀 Monitoring {len(mentions)} mentions")
        
        prioritized = []
        
        for mention in mentions:
            user = mention.get("user", {})
            text = mention.get("text", "")
            followers = user.get("followers_count", 0)
            
            # Determine priority and action
            priority = "low"
            action = "monitor"
            
            # High priority: Existing customers or high-follower accounts
            if followers > 500 or mention.get("is_customer", False):
                priority = "high"
                action = "reply_immediately"
            
            # Medium priority: Mentions CS/churn topics
            elif any(keyword in text.lower() for keyword in ["churn", "customer success", "cs", "retention"]):
                priority = "medium"
                action = "reply_within_24h"
            
            # Generate suggested reply
            suggested_reply = await self.generate_reply(
                original_tweet=text,
                context=f"User has {followers} followers"
            )
            
            prioritized.append({
                "mention_id": mention.get("id"),
                "user": user.get("username"),
                "text": text,
                "followers": followers,
                "priority": priority,
                "action": action,
                "suggested_reply": suggested_reply
            })
        
        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        prioritized.sort(key=lambda x: priority_order[x["priority"]])
        
        logger.info(f"✅ Prioritized mentions: {sum(1 for m in prioritized if m['priority'] == 'high')} high priority")
        
        return prioritized
    
    async def analyze_engagement(
        self,
        posts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Analyze post engagement and identify top performers
        
        Args:
            posts: List of posts with engagement metrics
        
        Returns:
            Engagement analysis
        """
        logger.info(f"📊 Analyzing engagement for {len(posts)} posts")
        
        if not posts:
            return {"total_posts": 0, "top_performers": [], "insights": []}
        
        # Calculate engagement rate for each post
        for post in posts:
            impressions = post.get("impressions", 1)
            engagements = post.get("likes", 0) + post.get("retweets", 0) + post.get("replies", 0)
            post["engagement_rate"] = (engagements / impressions * 100) if impressions > 0 else 0
        
        # Sort by engagement rate
        posts.sort(key=lambda x: x["engagement_rate"], reverse=True)
        
        top_performers = posts[:5]
        
        # Generate insights
        insights = []
        
        avg_engagement = sum(p["engagement_rate"] for p in posts) / len(posts)
        insights.append(f"Average engagement rate: {avg_engagement:.2f}%")
        
        # Identify best-performing content types
        thread_posts = [p for p in posts if p.get("is_thread", False)]
        if thread_posts:
            thread_avg = sum(p["engagement_rate"] for p in thread_posts) / len(thread_posts)
            insights.append(f"Threads perform {thread_avg/avg_engagement:.1f}x better than single tweets")
        
        logger.info(f"✅ Analysis complete: {len(top_performers)} top performers identified")
        
        return {
            "total_posts": len(posts),
            "average_engagement_rate": round(avg_engagement, 2),
            "top_performers": top_performers,
            "insights": insights
        }


# Global social agent instance
social_agent = SocialAgent()
