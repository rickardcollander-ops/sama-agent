"""
Reddit Integration for Social Agent
Handles Reddit posting, engagement monitoring, and competitor tracking.
Uses Reddit OAuth2 (script-type) via httpx - no external reddit library required.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import httpx
import base64

from shared.config import settings
from shared.database import get_supabase
from agents.brand_voice import brand_voice

logger = logging.getLogger(__name__)

REDDIT_API = "https://oauth.reddit.com"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Subreddits relevant to Successifier's target audience
TARGET_SUBREDDITS = [
    "CustomerSuccess",
    "SaaS",
    "startups",
    "B2BSaaS",
    "sales",
    "smallbusiness",
    "Entrepreneur",
]

# Search terms for finding relevant discussions
RELEVANT_SEARCH_TERMS = [
    "customer churn",
    "churn rate",
    "customer success",
    "customer retention SaaS",
    "NRR expansion revenue",
    "customer health score",
]

# Competitor search terms
COMPETITOR_SEARCH_TERMS = [
    "Gainsight alternative",
    "Totango alternative",
    "ChurnZero alternative",
    "Gainsight expensive",
    "Totango pricing",
]


def is_reddit_configured() -> bool:
    """Check if Reddit API credentials are configured"""
    return bool(
        settings.REDDIT_CLIENT_ID
        and settings.REDDIT_CLIENT_SECRET
        and settings.REDDIT_USERNAME
        and settings.REDDIT_PASSWORD
    )


class RedditManager:
    """
    Manage Reddit presence for Successifier:
    - Submit posts and comments to relevant subreddits
    - Search for relevant discussions
    - Monitor brand/competitor mentions
    - Generate Reddit-appropriate long-form content
    - Analyze post performance
    """

    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ── Reddit OAuth2 (script-type) ────────────────────────────────────

    async def _get_access_token(self) -> Optional[str]:
        """Fetch or refresh Reddit OAuth2 access token using password grant"""
        import time

        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        if not is_reddit_configured():
            return None

        credentials = base64.b64encode(
            f"{settings.REDDIT_CLIENT_ID}:{settings.REDDIT_CLIENT_SECRET}".encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {credentials}",
            "User-Agent": settings.REDDIT_USER_AGENT,
        }
        data = {
            "grant_type": "password",
            "username": settings.REDDIT_USERNAME,
            "password": settings.REDDIT_PASSWORD,
            "scope": "submit read identity",
        }

        try:
            resp = await self.http_client.post(
                REDDIT_TOKEN_URL, headers=headers, data=data
            )
            if resp.status_code == 200:
                token_data = resp.json()
                self._access_token = token_data.get("access_token")
                self._token_expires_at = time.time() + token_data.get("expires_in", 3600)
                logger.info("Reddit access token fetched successfully")
                return self._access_token
            else:
                logger.error(f"Reddit token error: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Failed to get Reddit token: {e}")
            return None

    def _auth_headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": settings.REDDIT_USER_AGENT,
        }

    async def _reddit_get(self, endpoint: str, params: Dict = None) -> Dict:
        """Authenticated GET request to Reddit API"""
        token = await self._get_access_token()
        if not token:
            return {"error": "Reddit API not configured or token unavailable"}

        url = f"{REDDIT_API}{endpoint}"
        try:
            resp = await self.http_client.get(
                url, headers=self._auth_headers(token), params=params
            )
            if resp.status_code != 200:
                logger.warning(f"Reddit GET error: {resp.status_code} {resp.text[:200]}")
                return {"error": f"API returned {resp.status_code}"}
            return resp.json()
        except Exception as e:
            logger.error(f"Reddit GET failed: {e}")
            return {"error": str(e)}

    async def _reddit_post(self, endpoint: str, data: Dict) -> Dict:
        """Authenticated POST request to Reddit API"""
        token = await self._get_access_token()
        if not token:
            return {"error": "Reddit API not configured or token unavailable"}

        url = f"{REDDIT_API}{endpoint}"
        try:
            resp = await self.http_client.post(
                url, headers=self._auth_headers(token), data=data
            )
            if resp.status_code not in (200, 201):
                logger.warning(f"Reddit POST error: {resp.status_code} {resp.text[:200]}")
                return {"error": f"API returned {resp.status_code}", "detail": resp.text[:200]}
            return resp.json()
        except Exception as e:
            logger.error(f"Reddit POST failed: {e}")
            return {"error": str(e)}

    # ── Submission ─────────────────────────────────────────────────────

    async def submit_text_post(
        self,
        subreddit: str,
        title: str,
        text: str,
    ) -> Dict[str, Any]:
        """
        Submit a text (self) post to a subreddit.

        Args:
            subreddit: Target subreddit (without r/ prefix)
            title: Post title
            text: Post body (markdown supported)

        Returns:
            Submission result with post URL
        """
        if not is_reddit_configured():
            return {
                "status": "not_configured",
                "message": "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD",
            }

        result = await self._reddit_post(
            "/api/submit",
            {
                "sr": subreddit,
                "kind": "self",
                "title": title,
                "text": text,
                "nsfw": "false",
                "spoiler": "false",
                "resubmit": "true",
            },
        )

        if "error" in result:
            return {"status": "error", "detail": result}

        # Reddit returns json.data.url on success
        post_url = result.get("json", {}).get("data", {}).get("url")
        post_id = result.get("json", {}).get("data", {}).get("id")

        if not post_url:
            return {"status": "error", "detail": result}

        logger.info(f"Reddit post submitted: {post_url}")

        # Persist to database
        try:
            sb = get_supabase()
            sb.table("social_posts").insert(
                {
                    "platform": "reddit",
                    "content": f"**{title}**\n\n{text}",
                    "post_id": post_id,
                    "status": "published",
                    "published_at": datetime.utcnow().isoformat(),
                }
            ).execute()
        except Exception as e:
            logger.warning(f"Could not save Reddit post to DB: {e}")

        return {
            "status": "published",
            "post_id": post_id,
            "post_url": post_url,
            "subreddit": subreddit,
            "title": title,
        }

    async def submit_link_post(
        self,
        subreddit: str,
        title: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Submit a link post to a subreddit.

        Args:
            subreddit: Target subreddit (without r/ prefix)
            title: Post title
            url: Link to share

        Returns:
            Submission result with post URL
        """
        if not is_reddit_configured():
            return {"status": "not_configured", "message": "Set REDDIT_* env vars"}

        result = await self._reddit_post(
            "/api/submit",
            {
                "sr": subreddit,
                "kind": "link",
                "title": title,
                "url": url,
                "nsfw": "false",
                "resubmit": "true",
            },
        )

        if "error" in result:
            return {"status": "error", "detail": result}

        post_url = result.get("json", {}).get("data", {}).get("url")
        post_id = result.get("json", {}).get("data", {}).get("id")

        if not post_url:
            return {"status": "error", "detail": result}

        logger.info(f"Reddit link post submitted: {post_url}")

        try:
            sb = get_supabase()
            sb.table("social_posts").insert(
                {
                    "platform": "reddit",
                    "content": title,
                    "link_url": url,
                    "post_id": post_id,
                    "status": "published",
                    "published_at": datetime.utcnow().isoformat(),
                }
            ).execute()
        except Exception as e:
            logger.warning(f"Could not save Reddit link post to DB: {e}")

        return {
            "status": "published",
            "post_id": post_id,
            "post_url": post_url,
            "subreddit": subreddit,
            "title": title,
            "link": url,
        }

    async def post_comment(self, parent_id: str, text: str) -> Dict[str, Any]:
        """
        Post a comment on a Reddit post or comment thread.

        Args:
            parent_id: Fullname of parent (e.g. t3_abc123 for a post)
            text: Comment text (markdown supported)

        Returns:
            Comment result
        """
        if not is_reddit_configured():
            return {"status": "not_configured", "message": "Set REDDIT_* env vars"}

        result = await self._reddit_post(
            "/api/comment",
            {"parent": parent_id, "text": text},
        )

        if "error" in result:
            return {"status": "error", "detail": result}

        comment_id = result.get("json", {}).get("data", {}).get("things", [{}])[0].get("data", {}).get("id")

        logger.info(f"Reddit comment posted: {comment_id}")
        return {"status": "published", "comment_id": comment_id, "parent_id": parent_id}

    # ── Discovery & monitoring ─────────────────────────────────────────

    async def search_relevant_posts(
        self, limit: int = 25
    ) -> List[Dict[str, Any]]:
        """
        Search for posts relevant to customer success and churn in target subreddits.

        Returns:
            List of posts sorted by relevance/upvotes
        """
        all_posts: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for query in RELEVANT_SEARCH_TERMS:
            result = await self._reddit_get(
                "/search",
                {
                    "q": query,
                    "sort": "relevance",
                    "t": "week",
                    "limit": 10,
                    "type": "link",
                    "restrict_sr": "false",
                },
            )

            children = (
                result.get("data", {}).get("children", [])
                if "data" in result
                else []
            )

            for child in children:
                post = child.get("data", {})
                pid = post.get("id")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                subreddit = post.get("subreddit", "")
                all_posts.append(
                    {
                        "id": pid,
                        "fullname": f"t3_{pid}",
                        "title": post.get("title", ""),
                        "selftext": post.get("selftext", "")[:500],
                        "subreddit": subreddit,
                        "author": post.get("author", ""),
                        "score": post.get("score", 0),
                        "num_comments": post.get("num_comments", 0),
                        "url": post.get("url", ""),
                        "permalink": f"https://reddit.com{post.get('permalink', '')}",
                        "created_utc": post.get("created_utc"),
                        "query": query,
                    }
                )

        all_posts.sort(key=lambda p: p["score"], reverse=True)
        logger.info(f"Found {len(all_posts)} relevant Reddit posts")
        return all_posts[:limit]

    async def search_competitor_mentions(
        self, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Search for posts mentioning competitors (Gainsight, Totango, ChurnZero).

        Returns:
            List of competitor mention posts
        """
        all_posts: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for query in COMPETITOR_SEARCH_TERMS:
            result = await self._reddit_get(
                "/search",
                {
                    "q": query,
                    "sort": "new",
                    "t": "month",
                    "limit": 10,
                    "type": "link",
                },
            )

            children = result.get("data", {}).get("children", [])
            for child in children:
                post = child.get("data", {})
                pid = post.get("id")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                all_posts.append(
                    {
                        "id": pid,
                        "fullname": f"t3_{pid}",
                        "title": post.get("title", ""),
                        "selftext": post.get("selftext", "")[:500],
                        "subreddit": post.get("subreddit", ""),
                        "author": post.get("author", ""),
                        "score": post.get("score", 0),
                        "num_comments": post.get("num_comments", 0),
                        "permalink": f"https://reddit.com{post.get('permalink', '')}",
                        "created_utc": post.get("created_utc"),
                        "query": query,
                    }
                )

        all_posts.sort(key=lambda p: p["score"], reverse=True)
        logger.info(f"Found {len(all_posts)} competitor mentions on Reddit")
        return all_posts[:limit]

    async def get_mentions(self, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Fetch Reddit mentions of the authenticated account.

        Returns:
            List of mention messages/comments
        """
        result = await self._reddit_get(
            "/message/mentions", {"limit": limit, "mark": "false"}
        )

        if "error" in result:
            return []

        mentions = []
        for child in result.get("data", {}).get("children", []):
            m = child.get("data", {})
            mentions.append(
                {
                    "id": m.get("id"),
                    "fullname": m.get("name"),
                    "subject": m.get("subject", ""),
                    "body": m.get("body", ""),
                    "author": m.get("author", ""),
                    "subreddit": m.get("subreddit", ""),
                    "created_utc": m.get("created_utc"),
                    "context": f"https://reddit.com{m.get('context', '')}",
                }
            )

        logger.info(f"Fetched {len(mentions)} Reddit mentions")
        return mentions

    async def get_subreddit_hot(
        self, subreddit: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Fetch hot posts from a specific subreddit.

        Args:
            subreddit: Subreddit name (without r/ prefix)
            limit: Number of posts to return

        Returns:
            List of hot posts
        """
        result = await self._reddit_get(
            f"/r/{subreddit}/hot",
            {"limit": limit},
        )

        if "error" in result:
            return []

        posts = []
        for child in result.get("data", {}).get("children", []):
            post = child.get("data", {})
            posts.append(
                {
                    "id": post.get("id"),
                    "fullname": f"t3_{post.get('id')}",
                    "title": post.get("title", ""),
                    "selftext": post.get("selftext", "")[:500],
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "author": post.get("author", ""),
                    "permalink": f"https://reddit.com{post.get('permalink', '')}",
                    "created_utc": post.get("created_utc"),
                }
            )

        return posts

    # ── AI content generation ──────────────────────────────────────────

    async def generate_reddit_post(
        self,
        topic: str,
        subreddit: str = "CustomerSuccess",
        post_type: str = "educational",
    ) -> Dict[str, Any]:
        """
        Generate a Reddit post using Claude AI, tailored to Reddit's culture.

        Reddit requires value-first content - no blatant marketing.
        Posts should be detailed, helpful, and start discussions.

        Args:
            topic: What to write about
            subreddit: Target subreddit for tone calibration
            post_type: educational | case_study | question | tips

        Returns:
            Generated post with title and body
        """
        from anthropic import Anthropic

        if not settings.ANTHROPIC_API_KEY:
            return {"error": "Anthropic API key not configured"}

        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        system_prompt = brand_voice.get_system_prompt("blog")

        user_prompt = f"""Create a Reddit post for r/{subreddit} about: {topic}

Post type: {post_type}

Reddit-specific requirements:
- Title: compelling but not clickbait, 60-120 characters
- Body: 200-600 words, detailed and value-driven
- Write as a practitioner sharing experience, NOT as a company
- Reddit users are allergic to marketing - focus on insights and lessons
- Use first-person perspective ("We reduced churn by...", "I noticed that...")
- Include specific numbers and data points where possible
- Invite discussion with a question at the end
- NO promotional language or CTAs to buy anything
- Markdown formatting is fine (use sparingly)

Successifier context (weave in naturally only if directly relevant):
- 40% churn reduction results
- 25% NRR improvement
- AI-native customer success platform
- $79/month starting price

Return as JSON:
{{
  "title": "Post title here",
  "body": "Full post body here",
  "flair": "optional flair tag"
}}"""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1200,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            content = response.content[0].text.strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            result = json.loads(content)
            result["subreddit"] = subreddit
            result["post_type"] = post_type
            result["topic"] = topic
            result["status"] = "draft"

            # Save draft to content_pieces
            try:
                sb = get_supabase()
                sb.table("content_pieces").insert(
                    {
                        "title": result["title"],
                        "content_type": "reddit_post",
                        "content": result["body"],
                        "status": "draft",
                        "word_count": len(result["body"].split()),
                        "created_by": "sama_social_reddit",
                    }
                ).execute()
            except Exception as e:
                logger.warning(f"Could not save Reddit draft to DB: {e}")

            logger.info(f"Generated Reddit post for r/{subreddit}: {result['title'][:60]}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Reddit post JSON: {e}")
            return {"error": "Failed to parse AI response as JSON", "raw": content}
        except Exception as e:
            logger.error(f"Reddit post generation failed: {e}")
            return {"error": str(e)}

    async def generate_reddit_comment(
        self,
        post_title: str,
        post_body: str,
        subreddit: str = "CustomerSuccess",
    ) -> str:
        """
        Generate a helpful Reddit comment in response to a post.

        Args:
            post_title: Original post title
            post_body: Original post body
            subreddit: Context subreddit

        Returns:
            Comment text (markdown)
        """
        from anthropic import Anthropic

        if not settings.ANTHROPIC_API_KEY:
            return "Comment generation requires Anthropic API key."

        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        system_prompt = brand_voice.get_system_prompt("blog")

        user_prompt = f"""Write a helpful Reddit comment for this post in r/{subreddit}:

Title: {post_title}
Body: {post_body[:800]}

Requirements:
- 80-200 words
- Genuinely helpful, adds value to the discussion
- First-person practitioner perspective
- Specific and actionable, not generic
- Can mention Successifier only if it directly and naturally answers the question
- No sales pitches - Reddit detects and downvotes them
- End with a follow-up question or additional insight

Format as plain text (markdown is OK for code/lists).
"""

        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        comment = response.content[0].text.strip()
        logger.info("Generated Reddit comment")
        return comment

    async def generate_and_submit(
        self,
        topic: str,
        subreddit: str = "CustomerSuccess",
        post_type: str = "educational",
    ) -> Dict[str, Any]:
        """
        Generate and immediately submit a Reddit post.

        Args:
            topic: Post topic
            subreddit: Target subreddit
            post_type: educational | case_study | question | tips

        Returns:
            Submission result
        """
        post = await self.generate_reddit_post(topic, subreddit, post_type)

        if "error" in post:
            return post

        if not is_reddit_configured():
            post["status"] = "draft_only"
            post["message"] = "Reddit API not configured. Set REDDIT_* env vars to enable posting."
            return post

        submit_result = await self.submit_text_post(
            subreddit=subreddit,
            title=post["title"],
            text=post["body"],
        )

        post.update(submit_result)
        return post


# Global instance
reddit_manager = RedditManager()
