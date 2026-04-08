"""
Social Posts API Routes
Listing and creation of social posts, scoped by tenant.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class SocialPostCreate(BaseModel):
    platform: str
    content: str
    content_type: Optional[str] = "post"
    topic: Optional[str] = None
    style: Optional[str] = None


def _ensure_numeric(row: dict) -> dict:
    """Replace null numeric fields with 0."""
    for key in ("likes", "comments", "shares", "impressions"):
        if row.get(key) is None:
            row[key] = 0
    if row.get("engagement_rate") is None:
        row["engagement_rate"] = 0.0
    return row


# ── List posts ───────────────────────────────────────────────────────────────

@router.get("/posts")
async def list_social_posts(request: Request, limit: int = 50):
    """List social posts for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_SOCIAL_POSTS
        return {"posts": DEMO_SOCIAL_POSTS}

    try:
        sb = get_supabase()
        result = (
            sb.table("social_posts")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        posts = [_ensure_numeric(r) for r in (result.data or [])]
        return {"posts": posts}
    except Exception as e:
        logger.error(f"list_social_posts error: {e}")
        return {"posts": []}


# ── Create post ─────────────────────────────────────────────────────────────

@router.post("/posts")
async def create_social_post(request: Request, payload: SocialPostCreate):
    """Create a new draft social post."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    try:
        sb = get_supabase()
        data = {
            **payload.model_dump(),
            "tenant_id": tenant_id,
            "status": "draft",
        }
        result = sb.table("social_posts").insert(data).execute()
        row = result.data[0] if result.data else data
        return {"success": True, "post": _ensure_numeric(row)}
    except Exception as e:
        logger.error(f"create_social_post error: {e}")
        return {"success": False, "error": str(e)}


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def social_stats(request: Request):
    """Aggregated social media stats for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_SOCIAL_POSTS
        posts = DEMO_SOCIAL_POSTS
        total_likes = sum(p.get("likes", 0) or 0 for p in posts)
        total_comments = sum(p.get("comments", 0) or 0 for p in posts)
        total_shares = sum(p.get("shares", 0) or 0 for p in posts)
        total_impressions = sum(p.get("impressions", 0) or 0 for p in posts)
        published = [p for p in posts if p.get("status") == "published"]
        avg_engagement = (
            sum(p.get("engagement_rate", 0) or 0 for p in published) / len(published)
            if published else 0
        )
        return {
            "total_posts": len(posts),
            "published": len(published),
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_shares": total_shares,
            "total_impressions": total_impressions,
            "avg_engagement_rate": round(avg_engagement, 2),
        }

    try:
        sb = get_supabase()
        result = (
            sb.table("social_posts")
            .select("*")
            .eq("tenant_id", tenant_id)
            .execute()
        )
        posts = result.data or []
        total_likes = sum((p.get("likes") or 0) for p in posts)
        total_comments = sum((p.get("comments") or 0) for p in posts)
        total_shares = sum((p.get("shares") or 0) for p in posts)
        total_impressions = sum((p.get("impressions") or 0) for p in posts)
        published = [p for p in posts if p.get("status") == "published"]
        avg_engagement = (
            sum((p.get("engagement_rate") or 0) for p in published) / len(published)
            if published else 0
        )
        return {
            "total_posts": len(posts),
            "published": len(published),
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_shares": total_shares,
            "total_impressions": total_impressions,
            "avg_engagement_rate": round(avg_engagement, 2),
        }
    except Exception as e:
        logger.error(f"social_stats error: {e}")
        return {
            "total_posts": 0,
            "published": 0,
            "total_likes": 0,
            "total_comments": 0,
            "total_shares": 0,
            "total_impressions": 0,
            "avg_engagement_rate": 0.0,
        }
