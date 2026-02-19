"""
Reddit API Routes
Endpoints for Reddit content generation, posting, and monitoring.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from agents.social_reddit import reddit_manager, is_reddit_configured, TARGET_SUBREDDITS

router = APIRouter()


# ── Request models ─────────────────────────────────────────────────────────────


class TextPostRequest(BaseModel):
    subreddit: str
    title: str
    text: str


class LinkPostRequest(BaseModel):
    subreddit: str
    title: str
    url: str


class CommentRequest(BaseModel):
    parent_id: str  # Reddit fullname, e.g. t3_abc123
    text: str


class GeneratePostRequest(BaseModel):
    topic: str
    subreddit: str = "CustomerSuccess"
    post_type: str = "educational"  # educational | case_study | question | tips


class GenerateCommentRequest(BaseModel):
    post_title: str
    post_body: str
    subreddit: str = "CustomerSuccess"


class GenerateAndSubmitRequest(BaseModel):
    topic: str
    subreddit: str = "CustomerSuccess"
    post_type: str = "educational"


class SubredditHotRequest(BaseModel):
    subreddit: str
    limit: int = 10


# ── Status ─────────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status():
    """Get Reddit integration status"""
    return {
        "platform": "reddit",
        "configured": is_reddit_configured(),
        "target_subreddits": TARGET_SUBREDDITS,
        "status": "operational" if is_reddit_configured() else "not_configured",
        "message": (
            None
            if is_reddit_configured()
            else "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD"
        ),
    }


# ── Content generation ─────────────────────────────────────────────────────────


@router.post("/generate")
async def generate_post(request: GeneratePostRequest):
    """Generate a Reddit post using AI (does not submit)"""
    try:
        result = await reddit_manager.generate_reddit_post(
            topic=request.topic,
            subreddit=request.subreddit,
            post_type=request.post_type,
        )
        return {"success": "error" not in result, "post": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-comment")
async def generate_comment(request: GenerateCommentRequest):
    """Generate a helpful comment for a Reddit post using AI"""
    try:
        comment = await reddit_manager.generate_reddit_comment(
            post_title=request.post_title,
            post_body=request.post_body,
            subreddit=request.subreddit,
        )
        return {"success": True, "comment": comment}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-and-submit")
async def generate_and_submit(request: GenerateAndSubmitRequest):
    """Generate a Reddit post and immediately submit it"""
    try:
        result = await reddit_manager.generate_and_submit(
            topic=request.topic,
            subreddit=request.subreddit,
            post_type=request.post_type,
        )
        success = result.get("status") in ("published", "draft_only")
        return {"success": success, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Submission ─────────────────────────────────────────────────────────────────


@router.post("/submit/text")
async def submit_text_post(request: TextPostRequest):
    """Submit a text post to a subreddit"""
    try:
        result = await reddit_manager.submit_text_post(
            subreddit=request.subreddit,
            title=request.title,
            text=request.text,
        )
        return {"success": result.get("status") == "published", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit/link")
async def submit_link_post(request: LinkPostRequest):
    """Submit a link post to a subreddit"""
    try:
        result = await reddit_manager.submit_link_post(
            subreddit=request.subreddit,
            title=request.title,
            url=request.url,
        )
        return {"success": result.get("status") == "published", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/comment")
async def post_comment(request: CommentRequest):
    """Post a comment on a Reddit post or comment thread"""
    try:
        result = await reddit_manager.post_comment(
            parent_id=request.parent_id,
            text=request.text,
        )
        return {"success": result.get("status") == "published", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Discovery & monitoring ─────────────────────────────────────────────────────


@router.get("/search/relevant")
async def search_relevant_posts(limit: int = 25):
    """Search for relevant customer success / churn posts across Reddit"""
    try:
        posts = await reddit_manager.search_relevant_posts(limit=limit)
        return {"success": True, "count": len(posts), "posts": posts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/competitors")
async def search_competitor_mentions(limit: int = 20):
    """Search for posts mentioning Gainsight, Totango, or ChurnZero"""
    try:
        posts = await reddit_manager.search_competitor_mentions(limit=limit)
        return {"success": True, "count": len(posts), "posts": posts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mentions")
async def get_mentions(limit: int = 25):
    """Fetch Reddit mentions of the authenticated account"""
    try:
        mentions = await reddit_manager.get_mentions(limit=limit)
        return {"success": True, "count": len(mentions), "mentions": mentions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subreddit/{subreddit}/hot")
async def get_subreddit_hot(subreddit: str, limit: int = 10):
    """Fetch hot posts from a specific subreddit"""
    try:
        posts = await reddit_manager.get_subreddit_hot(subreddit=subreddit, limit=limit)
        return {"success": True, "subreddit": subreddit, "count": len(posts), "posts": posts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subreddits")
async def list_target_subreddits():
    """List the target subreddits configured for Successifier"""
    return {"subreddits": TARGET_SUBREDDITS}
