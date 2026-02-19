"""
Social Agent - X/Twitter Management and Engagement
Manages social media presence for successifier.com
Uses Twitter/X API v2 for real posting, mentions, and engagement data.
"""

import json
import logging
import hashlib
import hmac
import base64
import time
import urllib.parse
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)

# Twitter API v2 endpoints
TWITTER_API = "https://api.twitter.com/2"


def is_twitter_configured() -> bool:
    """Check if Twitter API credentials are configured"""
    return bool(
        settings.TWITTER_BEARER_TOKEN
        or (settings.TWITTER_API_KEY and settings.TWITTER_API_SECRET
            and settings.TWITTER_ACCESS_TOKEN and settings.TWITTER_ACCESS_SECRET)
    )


def _oauth1_header(method: str, url: str, params: dict = None) -> str:
    """Generate OAuth 1.0a Authorization header for Twitter API"""
    import secrets
    
    oauth_params = {
        "oauth_consumer_key": settings.TWITTER_API_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": settings.TWITTER_ACCESS_TOKEN,
        "oauth_version": "1.0"
    }
    
    all_params = {**oauth_params}
    if params:
        all_params.update(params)
    
    # Create signature base string
    sorted_params = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}" 
                             for k, v in sorted(all_params.items()))
    base_string = f"{method.upper()}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    
    # Create signing key
    signing_key = f"{urllib.parse.quote(settings.TWITTER_API_SECRET, safe='')}&{urllib.parse.quote(settings.TWITTER_ACCESS_SECRET, safe='')}"
    
    # Generate signature
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    
    oauth_params["oauth_signature"] = signature
    
    header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    
    return header


class SocialAgent:
    """
    Social Agent responsible for:
    - X/Twitter post scheduling & publishing (Twitter API v2)
    - Engagement monitoring (real mentions)
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
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-sonnet-4-5-20250929"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
    
    # ── Twitter API v2 methods ─────────────────────────────────────────
    
    async def _twitter_get(self, endpoint: str, params: dict = None) -> Dict:
        """Make authenticated GET request to Twitter API v2"""
        url = f"{TWITTER_API}{endpoint}"
        headers = {}
        
        if settings.TWITTER_API_KEY and settings.TWITTER_ACCESS_TOKEN:
            headers["Authorization"] = _oauth1_header("GET", url, params)
        elif settings.TWITTER_BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {settings.TWITTER_BEARER_TOKEN}"
        else:
            return {"error": "Twitter API not configured"}
        
        resp = await self.http_client.get(url, params=params, headers=headers)
        
        if resp.status_code != 200:
            logger.warning(f"Twitter API error: {resp.status_code} {resp.text[:300]}")
            return {"error": f"API returned {resp.status_code}"}
        
        return resp.json()
    
    async def _twitter_post(self, endpoint: str, body: dict) -> Dict:
        """Make authenticated POST request to Twitter API v2 (requires OAuth 1.0a)"""
        url = f"{TWITTER_API}{endpoint}"
        
        if not settings.TWITTER_API_KEY or not settings.TWITTER_ACCESS_TOKEN:
            return {"error": "Twitter OAuth 1.0a credentials required for posting"}
        
        headers = {
            "Authorization": _oauth1_header("POST", url),
            "Content-Type": "application/json"
        }
        
        resp = await self.http_client.post(url, json=body, headers=headers)
        
        if resp.status_code not in (200, 201):
            logger.warning(f"Twitter POST error: {resp.status_code} {resp.text[:300]}")
            return {"error": f"API returned {resp.status_code}", "detail": resp.text[:300]}
        
        return resp.json()
    
    async def publish_tweet(self, text: str, reply_to: Optional[str] = None) -> Dict[str, Any]:
        """Publish a tweet via Twitter API v2"""
        if not is_twitter_configured():
            return {"status": "not_configured", "message": "Set TWITTER_API_KEY, TWITTER_ACCESS_TOKEN etc."}
        
        body = {"text": text[:280]}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        
        result = await self._twitter_post("/tweets", body)
        
        if "data" in result:
            tweet_id = result["data"]["id"]
            logger.info(f"✅ Tweet published: {tweet_id}")
            return {"status": "published", "tweet_id": tweet_id, "text": text[:280]}
        
        return {"status": "error", "detail": result}
    
    async def publish_thread(self, tweets: List[str]) -> List[Dict[str, Any]]:
        """Publish a thread (multiple tweets) via Twitter API v2"""
        results = []
        reply_to = None
        
        for i, tweet_text in enumerate(tweets):
            body = {"text": tweet_text[:280]}
            if reply_to:
                body["reply"] = {"in_reply_to_tweet_id": reply_to}
            
            result = await self._twitter_post("/tweets", body)
            
            if "data" in result:
                reply_to = result["data"]["id"]
                results.append({"status": "published", "tweet_id": reply_to, "text": tweet_text[:280], "position": i + 1})
            else:
                results.append({"status": "error", "position": i + 1, "detail": result})
                break
        
        logger.info(f"✅ Thread published: {len(results)} tweets")
        return results
    
    async def get_mentions(self, max_results: int = 20) -> List[Dict[str, Any]]:
        """Fetch recent mentions via Twitter API v2"""
        if not is_twitter_configured():
            return []
        
        # First get our user ID
        me = await self._twitter_get("/users/me")
        if "data" not in me:
            return []
        
        user_id = me["data"]["id"]
        
        # Fetch mentions
        data = await self._twitter_get(f"/users/{user_id}/mentions", params={
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username,public_metrics"
        })
        
        if "data" not in data:
            return []
        
        # Build user lookup
        users = {}
        for u in data.get("includes", {}).get("users", []):
            users[u["id"]] = u
        
        mentions = []
        for tweet in data["data"]:
            author = users.get(tweet.get("author_id"), {})
            mentions.append({
                "id": tweet["id"],
                "text": tweet["text"],
                "created_at": tweet.get("created_at"),
                "user": {
                    "username": author.get("username", "unknown"),
                    "followers_count": author.get("public_metrics", {}).get("followers_count", 0)
                },
                "metrics": tweet.get("public_metrics", {})
            })
        
        logger.info(f"✅ Fetched {len(mentions)} mentions from Twitter")
        return mentions
    
    async def get_tweet_metrics(self, tweet_ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch engagement metrics for specific tweets"""
        if not tweet_ids or not is_twitter_configured():
            return []
        
        ids = ",".join(tweet_ids[:100])
        data = await self._twitter_get("/tweets", params={
            "ids": ids,
            "tweet.fields": "public_metrics,created_at"
        })
        
        if "data" not in data:
            return []
        
        results = []
        for tweet in data["data"]:
            metrics = tweet.get("public_metrics", {})
            results.append({
                "tweet_id": tweet["id"],
                "text": tweet.get("text", ""),
                "created_at": tweet.get("created_at"),
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "impressions": metrics.get("impression_count", 0),
                "quotes": metrics.get("quote_count", 0)
            })
        
        return results
    
    async def search_interesting_tweets(self, max_results: int = 20) -> List[Dict[str, Any]]:
        """Search for interesting tweets about customer success, churn, and related pain points"""
        if not settings.TWITTER_BEARER_TOKEN:
            return []

        # Topics that are relevant and where Successifier can add value
        queries = [
            '("customer success" OR "churn rate" OR "customer retention") (struggling OR challenge OR "how do you" OR "any tips" OR help) -is:retweet lang:en',
            '("customer success manager" OR "CSM") (burnout OR overwhelmed OR "too many accounts" OR manual) -is:retweet lang:en',
            '(churn OR "customer churn") ("Q1" OR "Q2" OR "Q3" OR "Q4" OR quarter) -is:retweet lang:en min_faves:5',
        ]

        all_tweets = []
        seen_ids = set()

        for query in queries:
            data = await self._twitter_get("/tweets/search/recent", params={
                "query": query,
                "max_results": min(10, max_results),
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "username,public_metrics,description"
            })

            if "data" not in data:
                continue

            users = {}
            for u in data.get("includes", {}).get("users", []):
                users[u["id"]] = u

            for tweet in data["data"]:
                if tweet["id"] in seen_ids:
                    continue
                seen_ids.add(tweet["id"])

                author = users.get(tweet.get("author_id"), {})
                metrics = tweet.get("public_metrics", {})
                engagement = metrics.get("like_count", 0) + metrics.get("retweet_count", 0) + metrics.get("reply_count", 0)

                all_tweets.append({
                    "id": tweet["id"],
                    "text": tweet["text"],
                    "created_at": tweet.get("created_at"),
                    "user": {
                        "username": author.get("username", "unknown"),
                        "followers_count": author.get("public_metrics", {}).get("followers_count", 0),
                        "description": author.get("description", "")
                    },
                    "metrics": metrics,
                    "engagement_score": engagement,
                    "tweet_url": f"https://twitter.com/{author.get('username', 'unknown')}/status/{tweet['id']}"
                })

        # Sort by engagement
        all_tweets.sort(key=lambda t: t["engagement_score"], reverse=True)

        logger.info(f"✅ Found {len(all_tweets)} interesting tweets")
        return all_tweets[:max_results]

    async def search_competitor_mentions(self, max_results: int = 20) -> List[Dict[str, Any]]:
        """Search for competitor complaint tweets"""
        if not settings.TWITTER_BEARER_TOKEN:
            return []
        
        query = '("gainsight" OR "totango" OR "churnzero") (frustrating OR expensive OR slow OR "looking for alternative" OR "switching from") -is:retweet lang:en'
        
        data = await self._twitter_get("/tweets/search/recent", params={
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username,public_metrics"
        })
        
        if "data" not in data:
            return []
        
        users = {}
        for u in data.get("includes", {}).get("users", []):
            users[u["id"]] = u
        
        results = []
        for tweet in data["data"]:
            author = users.get(tweet.get("author_id"), {})
            results.append({
                "id": tweet["id"],
                "text": tweet["text"],
                "created_at": tweet.get("created_at"),
                "user": {
                    "username": author.get("username", "unknown"),
                    "followers_count": author.get("public_metrics", {}).get("followers_count", 0)
                }
            })
        
        logger.info(f"✅ Found {len(results)} competitor-related tweets")
        return results
    
    # ── Content generation (existing + enhanced) ───────────────────────
    
    async def generate_post(
        self,
        topic: str,
        style: str = "educational",
        thread: bool = False
    ) -> Dict[str, Any]:
        """Generate X/Twitter post"""
        logger.info(f"🐦 Generating Twitter post: {topic}")
        
        if not self.client:
            return {"error": "Anthropic API key not configured", "status": "error"}
        
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
            try:
                tweets = json.loads(content)
            except:
                tweets = [t.strip() for t in content.split('\n') if t.strip()]
        else:
            tweets = [content]
        
        tweets = [t[:280] for t in tweets]

        logger.info(f"✅ Generated {len(tweets)} tweet(s)")

        result = {
            "topic": topic,
            "style": style,
            "is_thread": thread,
            "tweets": tweets,
            "status": "draft",
            "api_configured": is_twitter_configured()
        }

        # Save to content_pieces table
        try:
            sb = get_supabase()
            content_text = "\n\n".join(tweets)
            title = topic[:200]
            sb.table("content_pieces").insert({
                "title": title,
                "content_type": "thread" if thread else "tweet",
                "content": content_text,
                "target_keyword": style,
                "status": "draft",
                "word_count": len(content_text.split()),
                "created_by": "sama_social"
            }).execute()
            logger.info(f"✅ Saved content to content_pieces: {title[:50]}")
        except Exception as e:
            logger.warning(f"⚠️ Could not save to content_pieces: {e}")

        return result
    
    async def generate_and_publish(
        self,
        topic: str,
        style: str = "educational",
        thread: bool = False
    ) -> Dict[str, Any]:
        """Generate and immediately publish a tweet/thread"""
        post = await self.generate_post(topic, style, thread)
        
        if post.get("error"):
            return post
        
        if not is_twitter_configured():
            post["status"] = "draft_only"
            post["message"] = "Twitter API not configured. Set TWITTER_* env vars to enable publishing."
            return post
        
        if thread and len(post["tweets"]) > 1:
            publish_results = await self.publish_thread(post["tweets"])
            post["publish_results"] = publish_results
            post["status"] = "published" if all(r.get("status") == "published" for r in publish_results) else "partial"
        else:
            result = await self.publish_tweet(post["tweets"][0])
            post["publish_result"] = result
            post["status"] = result.get("status", "error")
        
        return post
    
    async def generate_reply(
        self,
        original_tweet: str,
        context: Optional[str] = None
    ) -> str:
        """Generate reply to a tweet"""
        logger.info(f"💬 Generating reply to tweet")
        
        if not self.client:
            return "Reply generation requires Anthropic API key."
        
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
    
    async def schedule_posts(self, date_range: int = 7) -> List[Dict[str, Any]]:
        """Generate content calendar for next N days"""
        logger.info(f"📅 Generating {date_range}-day content calendar")
        
        scheduled_posts = []
        start_date = datetime.now()
        
        for day_offset in range(date_range):
            post_date = start_date + timedelta(days=day_offset)
            day_name = post_date.strftime("%A").lower()
            
            if day_name in self.CONTENT_CALENDAR:
                day_config = self.CONTENT_CALENDAR[day_name]
                
                post = await self.generate_post(
                    topic=day_config["example"],
                    style="educational",
                    thread=day_config["format"] == "Educational thread"
                )
                
                scheduled_entry = {
                    "date": post_date.strftime("%Y-%m-%d"),
                    "day": day_name,
                    "theme": day_config["theme"],
                    "format": day_config["format"],
                    "post": post,
                    "scheduled_time": "09:00"
                }

                # Save to content_pieces
                try:
                    sb = get_supabase()
                    content_text = "\n\n".join(post.get("tweets", []))
                    sb.table("content_pieces").insert({
                        "title": f"[{post_date.strftime('%Y-%m-%d')}] {day_config['theme']}",
                        "content_type": "thread" if post.get("is_thread") else "tweet",
                        "content": content_text,
                        "status": "draft",
                        "word_count": len(content_text.split()),
                        "created_by": "sama_social"
                    }).execute()
                except Exception as e:
                    logger.warning(f"⚠️ Could not save calendar post: {e}")

                scheduled_posts.append(scheduled_entry)

        logger.info(f"✅ Generated {len(scheduled_posts)} scheduled posts")
        return scheduled_posts
    
    async def monitor_mentions(
        self,
        mentions: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Monitor and prioritize mentions - fetches real data if not provided"""
        # Fetch real mentions if not provided
        if mentions is None:
            mentions = await self.get_mentions(max_results=20)
        
        logger.info(f"👀 Monitoring {len(mentions)} mentions")
        
        prioritized = []
        
        for mention in mentions:
            user = mention.get("user", {})
            text = mention.get("text", "")
            followers = user.get("followers_count", 0)
            
            priority = "low"
            action = "monitor"
            
            if followers > 500 or mention.get("is_customer", False):
                priority = "high"
                action = "reply_immediately"
            elif any(keyword in text.lower() for keyword in ["churn", "customer success", "cs", "retention"]):
                priority = "medium"
                action = "reply_within_24h"
            
            suggested_reply = await self.generate_reply(
                original_tweet=text,
                context=f"User has {followers} followers"
            ) if self.client else ""
            
            prioritized.append({
                "mention_id": mention.get("id"),
                "user": user.get("username"),
                "text": text,
                "followers": followers,
                "priority": priority,
                "action": action,
                "suggested_reply": suggested_reply
            })
        
        priority_order = {"high": 0, "medium": 1, "low": 2}
        prioritized.sort(key=lambda x: priority_order[x["priority"]])
        
        logger.info(f"✅ Prioritized mentions: {sum(1 for m in prioritized if m['priority'] == 'high')} high priority")
        return prioritized
    
    async def analyze_engagement(
        self,
        posts: Optional[List[Dict[str, Any]]] = None,
        tweet_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Analyze post engagement - uses real metrics if tweet_ids provided"""
        # Fetch real metrics if tweet_ids provided
        if tweet_ids and not posts:
            posts = await self.get_tweet_metrics(tweet_ids)
        
        if not posts:
            return {"total_posts": 0, "top_performers": [], "insights": [], "api_configured": is_twitter_configured()}
        
        logger.info(f"📊 Analyzing engagement for {len(posts)} posts")
        
        for post in posts:
            impressions = post.get("impressions", 1)
            engagements = post.get("likes", 0) + post.get("retweets", 0) + post.get("replies", 0)
            post["engagement_rate"] = (engagements / impressions * 100) if impressions > 0 else 0
        
        posts.sort(key=lambda x: x["engagement_rate"], reverse=True)
        top_performers = posts[:5]
        
        insights = []
        avg_engagement = sum(p["engagement_rate"] for p in posts) / len(posts)
        insights.append(f"Average engagement rate: {avg_engagement:.2f}%")
        
        thread_posts = [p for p in posts if p.get("is_thread", False)]
        if thread_posts:
            thread_avg = sum(p["engagement_rate"] for p in thread_posts) / len(thread_posts)
            if avg_engagement > 0:
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
