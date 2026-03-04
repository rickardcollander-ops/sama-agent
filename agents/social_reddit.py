"""
Reddit Integration Agent - Reddit Management and Engagement
Manages Reddit presence for successifier.com
Uses Reddit OAuth2 API (script app type) with username/password grant.
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

import httpx
from anthropic import Anthropic

from shared.config import settings
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)

# Reddit API endpoints
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
REDDIT_USER_AGENT = "sama-agent/1.0 by Successifier"

# Default target subreddits for customer success / SaaS topics
DEFAULT_TARGET_SUBREDDITS = [
    "SaaS",
    "CustomerSuccess",
    "startups",
    "Entrepreneur",
    "smallbusiness",
]

# Competitor names for search
COMPETITOR_NAMES = [
    "Gainsight",
    "ChurnZero",
    "Totango",
    "Planhat",
    "Vitally",
]

# Search queries for relevant discussions
RELEVANT_SEARCH_QUERIES = [
    "customer success",
    "churn prevention",
    "customer health score",
    "customer retention SaaS",
    "reduce churn",
]


def is_reddit_configured() -> bool:
    """Check if Reddit API credentials are configured."""
    return bool(
        settings.REDDIT_CLIENT_ID
        and settings.REDDIT_CLIENT_SECRET
        and settings.REDDIT_USERNAME
        and settings.REDDIT_PASSWORD
    )


class RedditAgent:
    """
    Reddit Agent responsible for:
    - Searching relevant subreddits for CS / SaaS discussions
    - Monitoring competitor mentions on Reddit
    - Generating and submitting posts via AI
    - Generating and submitting comments via AI
    - Fetching hot posts from target subreddits
    """

    def __init__(
        self,
        target_subreddits: Optional[List[str]] = None,
    ):
        self.target_subreddits = target_subreddits or DEFAULT_TARGET_SUBREDDITS
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.client = (
            Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            if settings.ANTHROPIC_API_KEY
            else None
        )
        self.model = settings.CLAUDE_MODEL or "claude-sonnet-4-20250514"
        self.brand_voice = brand_voice

        # Token cache
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    # -- OAuth2 Token Management ------------------------------------------------

    async def _get_access_token(self) -> Optional[str]:
        """Obtain or return cached Reddit OAuth2 access token (password grant)."""
        import time

        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        if not is_reddit_configured():
            return None

        try:
            resp = await self.http_client.post(
                REDDIT_TOKEN_URL,
                auth=(settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET),
                data={
                    "grant_type": "password",
                    "username": settings.REDDIT_USERNAME,
                    "password": settings.REDDIT_PASSWORD,
                },
                headers={"User-Agent": REDDIT_USER_AGENT},
            )

            if resp.status_code != 200:
                logger.warning(
                    f"Reddit token request failed: {resp.status_code} {resp.text[:300]}"
                )
                return None

            body = resp.json()
            self._access_token = body.get("access_token")
            expires_in = body.get("expires_in", 3600)
            self._token_expires_at = time.time() + expires_in
            logger.info("Reddit OAuth2 token acquired")
            return self._access_token

        except Exception as exc:
            logger.error(f"Reddit OAuth2 error: {exc}")
            return None

    # -- Low-level HTTP helpers -------------------------------------------------

    async def _reddit_get(self, endpoint: str, params: Optional[dict] = None) -> Dict:
        """Authenticated GET to oauth.reddit.com."""
        token = await self._get_access_token()
        if not token:
            return {"error": "Reddit API not configured or token unavailable"}

        url = f"{REDDIT_API}{endpoint}"
        headers = {
            "Authorization": f"bearer {token}",
            "User-Agent": REDDIT_USER_AGENT,
        }

        resp = await self.http_client.get(url, params=params, headers=headers)

        if resp.status_code != 200:
            logger.warning(
                f"Reddit GET {endpoint} error: {resp.status_code} {resp.text[:300]}"
            )
            return {"error": f"Reddit API returned {resp.status_code}"}

        return resp.json()

    async def _reddit_post(self, endpoint: str, data: Optional[dict] = None) -> Dict:
        """Authenticated POST to oauth.reddit.com (form-encoded as Reddit expects)."""
        token = await self._get_access_token()
        if not token:
            return {"error": "Reddit API not configured or token unavailable"}

        url = f"{REDDIT_API}{endpoint}"
        headers = {
            "Authorization": f"bearer {token}",
            "User-Agent": REDDIT_USER_AGENT,
        }

        resp = await self.http_client.post(url, data=data, headers=headers)

        if resp.status_code not in (200, 201):
            logger.warning(
                f"Reddit POST {endpoint} error: {resp.status_code} {resp.text[:300]}"
            )
            return {
                "error": f"Reddit API returned {resp.status_code}",
                "detail": resp.text[:300],
            }

        return resp.json()

    # -- Helper: parse listing --------------------------------------------------

    @staticmethod
    def _parse_listing(data: Dict, kind: str = "t3") -> List[Dict[str, Any]]:
        """Parse a Reddit listing response into a flat list of post/comment dicts.
        kind t3 = link (post), t1 = comment.
        """
        items: List[Dict[str, Any]] = []
        children = (
            data.get("data", {}).get("children", [])
            if isinstance(data, dict)
            else []
        )

        for child in children:
            if child.get("kind") not in (kind, None):
                continue
            d = child.get("data", {})
            if kind == "t3":
                items.append(
                    {
                        "id": d.get("id", ""),
                        "fullname": child.get("kind", "t3") + "_" + d.get("id", ""),
                        "title": d.get("title", ""),
                        "selftext": d.get("selftext", "")[:1000],
                        "subreddit": d.get("subreddit", ""),
                        "author": d.get("author", "[deleted]"),
                        "score": d.get("score", 0),
                        "num_comments": d.get("num_comments", 0),
                        "permalink": d.get("permalink", ""),
                        "created_utc": d.get("created_utc", 0),
                        "url": d.get("url", ""),
                    }
                )
            elif kind == "t1":
                items.append(
                    {
                        "id": d.get("id", ""),
                        "body": d.get("body", "")[:1000],
                        "author": d.get("author", "[deleted]"),
                        "subreddit": d.get("subreddit", ""),
                        "score": d.get("score", 0),
                        "permalink": d.get("permalink", ""),
                        "created_utc": d.get("created_utc", 0),
                    }
                )

        return items

    # -- Public API Methods -----------------------------------------------------

    async def get_status(self) -> Dict[str, Any]:
        """Return Reddit integration status."""
        configured = is_reddit_configured()
        status: Dict[str, Any] = {
            "configured": configured,
            "target_subreddits": self.target_subreddits,
            "status": "operational" if configured else "not_configured",
            "message": (
                None
                if configured
                else "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD to enable Reddit integration."
            ),
        }

        if configured:
            # Validate token works
            token = await self._get_access_token()
            if not token:
                status["status"] = "auth_error"
                status["message"] = (
                    "Reddit credentials are set but authentication failed. Check your credentials."
                )

        return status

    async def get_subreddits(self) -> List[str]:
        """Return the list of target subreddits."""
        return list(self.target_subreddits)

    async def search_relevant(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Search for posts relevant to customer success / churn prevention."""
        if not is_reddit_configured():
            return []

        all_posts: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for query in RELEVANT_SEARCH_QUERIES:
            data = await self._reddit_get(
                "/search",
                params={
                    "q": query,
                    "sort": "relevance",
                    "t": "week",
                    "type": "link",
                    "limit": min(limit, 25),
                },
            )

            if "error" in data:
                continue

            for post in self._parse_listing(data):
                if post["id"] not in seen_ids:
                    seen_ids.add(post["id"])
                    all_posts.append(post)

        # Sort by score descending
        all_posts.sort(key=lambda p: p["score"], reverse=True)
        logger.info(f"Found {len(all_posts)} relevant Reddit posts")
        return all_posts[:limit]

    async def search_competitors(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for competitor mentions on Reddit."""
        if not is_reddit_configured():
            return []

        all_posts: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for query in COMPETITOR_NAMES:
            data = await self._reddit_get(
                "/search",
                params={
                    "q": query,
                    "sort": "new",
                    "t": "month",
                    "type": "link",
                    "limit": min(limit, 25),
                },
            )

            if "error" in data:
                continue

            for post in self._parse_listing(data):
                if post["id"] not in seen_ids:
                    seen_ids.add(post["id"])
                    all_posts.append(post)

        all_posts.sort(key=lambda p: p["created_utc"], reverse=True)
        logger.info(f"Found {len(all_posts)} competitor-related Reddit posts")
        return all_posts[:limit]

    async def get_mentions(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Get mentions of Successifier / related terms across Reddit.
        Uses search for 'successifier' as well as comment search.
        """
        if not is_reddit_configured():
            return []

        mentions: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Search for brand mentions in posts
        for query in ["successifier", "successifier.com"]:
            data = await self._reddit_get(
                "/search",
                params={
                    "q": query,
                    "sort": "new",
                    "t": "month",
                    "type": "link",
                    "limit": min(limit, 25),
                },
            )

            if "error" in data:
                continue

            for post in self._parse_listing(data):
                if post["id"] not in seen_ids:
                    seen_ids.add(post["id"])
                    mentions.append(
                        {
                            "id": post["id"],
                            "subject": post["title"],
                            "body": post["selftext"][:500],
                            "author": post["author"],
                            "subreddit": post["subreddit"],
                            "created_utc": post["created_utc"],
                            "context": f"https://reddit.com{post['permalink']}",
                        }
                    )

        # Also try comment search
        for query in ["successifier"]:
            data = await self._reddit_get(
                "/search",
                params={
                    "q": query,
                    "sort": "new",
                    "t": "month",
                    "type": "comment",
                    "limit": min(limit, 25),
                },
            )

            if "error" in data:
                continue

            for comment in self._parse_listing(data, kind="t1"):
                if comment["id"] not in seen_ids:
                    seen_ids.add(comment["id"])
                    mentions.append(
                        {
                            "id": comment["id"],
                            "subject": "Comment mention",
                            "body": comment["body"][:500],
                            "author": comment["author"],
                            "subreddit": comment["subreddit"],
                            "created_utc": comment["created_utc"],
                            "context": f"https://reddit.com{comment['permalink']}",
                        }
                    )

        mentions.sort(key=lambda m: m["created_utc"], reverse=True)
        logger.info(f"Found {len(mentions)} Reddit mentions")
        return mentions[:limit]

    async def get_hot_posts(
        self, subreddit: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get hot posts from a specific subreddit."""
        if not is_reddit_configured():
            return []

        data = await self._reddit_get(
            f"/r/{subreddit}/hot",
            params={"limit": min(limit, 100)},
        )

        if "error" in data:
            return []

        posts = self._parse_listing(data)
        logger.info(f"Fetched {len(posts)} hot posts from r/{subreddit}")
        return posts[:limit]

    async def generate_post(
        self,
        topic: str,
        subreddit: str,
        post_type: str = "educational",
    ) -> Dict[str, Any]:
        """Generate a Reddit post using Claude AI."""
        if not self.client:
            return {
                "error": "Anthropic API key not configured",
                "status": "error",
            }

        system_prompt = self.brand_voice.get_system_prompt("social")

        user_prompt = f"""Create a Reddit post for r/{subreddit} about: {topic}

Post type: {post_type}

Requirements:
- Title: concise, attention-grabbing, suitable for Reddit (no clickbait)
- Body: 150-500 words, value-driven, conversational
- Provide genuine insight or ask a thoughtful question
- Do NOT be overly promotional -- Reddit users downvote obvious marketing
- Reference data points if relevant (e.g. "40% churn reduction", "25% NRR improvement")
- Match the subreddit's culture (r/{subreddit})
- End with a question to encourage discussion
- Use markdown formatting where appropriate (Reddit supports markdown)

Return ONLY a JSON object:
{{
  "title": "Post title here",
  "body": "Post body text here (markdown OK)",
  "subreddit": "{subreddit}",
  "post_type": "{post_type}"
}}"""

        try:

            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )

            response = await asyncio.to_thread(_call)
            content = response.content[0].text.strip()

            # Try to parse JSON from the response
            try:
                # Handle potential markdown code blocks
                if "```" in content:
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    content = content[json_start:json_end]
                post_data = json.loads(content)
            except json.JSONDecodeError:
                # Fallback: use raw content
                post_data = {
                    "title": topic[:300],
                    "body": content,
                    "subreddit": subreddit,
                    "post_type": post_type,
                }

            post_data.setdefault("subreddit", subreddit)
            post_data.setdefault("post_type", post_type)
            post_data["status"] = "draft"

            logger.info(
                f"Generated Reddit post for r/{subreddit}: {post_data.get('title', '')[:60]}"
            )
            return post_data

        except Exception as exc:
            logger.error(f"Reddit post generation failed: {exc}")
            return {"error": str(exc), "status": "error"}

    async def submit_post(
        self,
        subreddit: str,
        title: str,
        text: str,
    ) -> Dict[str, Any]:
        """Submit a text post to a subreddit."""
        if not is_reddit_configured():
            return {
                "success": False,
                "error": "Reddit API not configured",
                "status": "not_configured",
            }

        result = await self._reddit_post(
            "/api/submit",
            data={
                "sr": subreddit,
                "kind": "self",
                "title": title,
                "text": text,
                "resubmit": "true",
            },
        )

        if "error" in result and isinstance(result["error"], str):
            return {"success": False, "error": result["error"], "status": "error"}

        # Reddit returns success info in json.data
        json_data = result.get("json", {})
        errors = json_data.get("errors", [])

        if errors:
            error_msg = "; ".join(str(e) for e in errors)
            logger.warning(f"Reddit submit errors: {error_msg}")
            return {"success": False, "error": error_msg, "status": "error"}

        data = json_data.get("data", {})
        post_url = data.get("url", "")
        post_id = data.get("id", "")
        post_name = data.get("name", "")

        logger.info(f"Reddit post submitted: {post_url}")

        return {
            "success": True,
            "status": "published",
            "url": post_url,
            "post_id": post_id,
            "post_name": post_name,
            "post_url": post_url,
        }

    async def generate_and_submit(
        self,
        topic: str,
        subreddit: str,
        post_type: str = "educational",
    ) -> Dict[str, Any]:
        """Generate a post with AI and immediately submit it."""
        post = await self.generate_post(topic, subreddit, post_type)

        if post.get("error"):
            return {"success": False, "error": post["error"], "result": post}

        if not is_reddit_configured():
            post["status"] = "draft_only"
            post["message"] = (
                "Reddit API not configured. Set REDDIT_* env vars to enable publishing."
            )
            return {"success": True, "url": "", "result": post}

        submit_result = await self.submit_post(
            subreddit=post.get("subreddit", subreddit),
            title=post.get("title", topic),
            text=post.get("body", ""),
        )

        return {
            "success": submit_result.get("success", False),
            "url": submit_result.get("post_url", ""),
            "result": {
                **post,
                "status": submit_result.get("status", "error"),
                "post_url": submit_result.get("post_url", ""),
                "post_id": submit_result.get("post_id", ""),
            },
        }

    async def generate_comment(
        self,
        post_title: str,
        post_body: str,
        subreddit: str,
    ) -> str:
        """Generate a comment for a Reddit post using Claude AI."""
        if not self.client:
            return "Comment generation requires Anthropic API key."

        system_prompt = self.brand_voice.get_system_prompt("social")

        user_prompt = f"""Generate a Reddit comment reply for a post in r/{subreddit}.

Post title: {post_title}
Post body: {post_body[:1500]}

Requirements:
- 50-200 words, concise and valuable
- Be genuinely helpful -- answer the question or add insight
- Conversational Reddit tone (not corporate)
- Do NOT be salesy or promotional unless the post explicitly asks for product recommendations
- If relevant, you can mention data points (e.g. "40% churn reduction") without naming Successifier directly unless asked
- No hashtags, no emojis overload
- Provide actionable advice or a thoughtful perspective

Return ONLY the comment text, no JSON wrapping."""

        try:

            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=512,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )

            response = await asyncio.to_thread(_call)
            comment = response.content[0].text.strip()
            logger.info(f"Generated Reddit comment ({len(comment)} chars)")
            return comment

        except Exception as exc:
            logger.error(f"Reddit comment generation failed: {exc}")
            return f"Error generating comment: {exc}"

    async def submit_comment(
        self,
        thing_id: str,
        text: str,
    ) -> Dict[str, Any]:
        """Submit a comment on a Reddit post or reply to a comment.

        thing_id should be the fullname of the thing being replied to,
        e.g. "t3_abc123" for a post or "t1_xyz789" for a comment.
        """
        if not is_reddit_configured():
            return {
                "success": False,
                "error": "Reddit API not configured",
                "status": "not_configured",
            }

        result = await self._reddit_post(
            "/api/comment",
            data={
                "thing_id": thing_id,
                "text": text,
            },
        )

        if "error" in result and isinstance(result["error"], str):
            return {"success": False, "error": result["error"], "status": "error"}

        json_data = result.get("json", {})
        errors = json_data.get("errors", [])

        if errors:
            error_msg = "; ".join(str(e) for e in errors)
            logger.warning(f"Reddit comment errors: {error_msg}")
            return {"success": False, "error": error_msg, "status": "error"}

        # Extract comment data
        things = json_data.get("data", {}).get("things", [])
        comment_data = things[0].get("data", {}) if things else {}
        comment_id = comment_data.get("id", "")

        logger.info(f"Reddit comment submitted: {comment_id}")

        return {
            "success": True,
            "status": "published",
            "comment_id": comment_id,
        }


# Global Reddit agent instance
reddit_agent = RedditAgent()
