"""
Reddit API Routes for SAMA 2.0
Provides endpoints for Reddit search, posting, commenting, and monitoring.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from agents.social_reddit import reddit_agent

router = APIRouter()


# ── Request Models ─────────────────────────────────────────────────────

class GeneratePostRequest(BaseModel):
    topic: str
    subreddit: str
    post_type: str = "educational"


class SubmitTextPostRequest(BaseModel):
    subreddit: str
    title: str
    text: str


class GenerateAndSubmitRequest(BaseModel):
    topic: str
    subreddit: str
    post_type: str = "educational"


class GenerateCommentRequest(BaseModel):
    post_title: str
    post_body: str = ""
    subreddit: str = ""


class SubmitCommentRequest(BaseModel):
    parent_id: str
    text: str


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/status")
async def get_reddit_status():
    """Get Reddit integration status and configuration info."""
    try:
        return await reddit_agent.get_status()
    except Exception as e:
        return {
            "configured": False,
            "target_subreddits": [],
            "status": "error",
            "message": str(e),
        }


@router.get("/subreddits")
async def get_subreddits():
    """Get the list of target subreddits."""
    try:
        subreddits = await reddit_agent.get_subreddits()
        return {"subreddits": subreddits}
    except Exception as e:
        return {"subreddits": [], "error": str(e)}


@router.get("/search/relevant")
async def search_relevant(limit: int = 25):
    """Search Reddit for posts relevant to customer success / churn prevention."""
    try:
        posts = await reddit_agent.search_relevant(limit=limit)
        return {"posts": posts, "count": len(posts)}
    except Exception as e:
        return {"posts": [], "count": 0, "error": str(e)}


@router.get("/search/competitors")
async def search_competitors(limit: int = 20):
    """Search Reddit for competitor mentions (Gainsight, Totango, ChurnZero)."""
    try:
        posts = await reddit_agent.search_competitors(limit=limit)
        return {"posts": posts, "count": len(posts)}
    except Exception as e:
        return {"posts": [], "count": 0, "error": str(e)}


@router.get("/mentions")
async def get_mentions(limit: int = 25):
    """Get mentions of Successifier on Reddit."""
    try:
        mentions = await reddit_agent.get_mentions(limit=limit)
        return {"mentions": mentions, "count": len(mentions)}
    except Exception as e:
        return {"mentions": [], "count": 0, "error": str(e)}


@router.get("/subreddit/{subreddit}/hot")
async def get_hot_posts(subreddit: str, limit: int = 10):
    """Get hot posts from a specific subreddit."""
    try:
        posts = await reddit_agent.get_hot_posts(subreddit=subreddit, limit=limit)
        return {"posts": posts, "count": len(posts), "subreddit": subreddit}
    except Exception as e:
        return {"posts": [], "count": 0, "subreddit": subreddit, "error": str(e)}


@router.post("/generate")
async def generate_post(request: GeneratePostRequest):
    """Generate a Reddit post using AI."""
    try:
        post = await reddit_agent.generate_post(
            topic=request.topic,
            subreddit=request.subreddit,
            post_type=request.post_type,
        )
        if post.get("error"):
            raise HTTPException(status_code=500, detail=post["error"])
        return {"success": True, "post": post}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit/text")
async def submit_text_post(request: SubmitTextPostRequest):
    """Submit a text post to a subreddit."""
    try:
        result = await reddit_agent.submit_post(
            subreddit=request.subreddit,
            title=request.title,
            text=request.text,
        )
        return {"success": result.get("success", False), "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-and-submit")
async def generate_and_submit(request: GenerateAndSubmitRequest):
    """Generate a post with AI and immediately submit it."""
    try:
        result = await reddit_agent.generate_and_submit(
            topic=request.topic,
            subreddit=request.subreddit,
            post_type=request.post_type,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-comment")
async def generate_comment(request: GenerateCommentRequest):
    """Generate a comment for a Reddit post using AI."""
    try:
        comment = await reddit_agent.generate_comment(
            post_title=request.post_title,
            post_body=request.post_body,
            subreddit=request.subreddit,
        )
        return {"success": True, "comment": comment}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/comment")
async def submit_comment(request: SubmitCommentRequest):
    """Submit a comment on a Reddit post."""
    try:
        result = await reddit_agent.submit_comment(
            parent_id=request.parent_id,
            text=request.text,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
