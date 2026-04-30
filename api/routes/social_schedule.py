"""
Social scheduling, engagement monitoring, and reply automation.

POST   /scheduled              — schedule a new post (status='scheduled', scheduled_for=ts)
GET    /scheduled              — list pending scheduled posts for the tenant
PATCH  /scheduled/{post_id}    — reschedule or cancel
DELETE /scheduled/{post_id}    — cancel and delete a scheduled post
POST   /scheduled/{post_id}/publish — manually publish now (worker also calls this)
GET    /engagement             — aggregated engagement metrics with trend
POST   /replies/draft          — generate AI reply drafts for pending mentions
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_PLATFORMS = {"twitter", "linkedin", "reddit", "x"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Models ──────────────────────────────────────────────────────────────────

class SchedulePostPayload(BaseModel):
    platform: str = Field(..., description="twitter, linkedin, reddit")
    content: str
    scheduled_for: datetime
    topic: Optional[str] = None
    style: Optional[str] = None


class ReschedulePayload(BaseModel):
    scheduled_for: Optional[datetime] = None
    content: Optional[str] = None
    cancel: bool = False


class ReplyDraftPayload(BaseModel):
    mention_text: str
    author: Optional[str] = None
    platform: str = "twitter"
    tone: str = "friendly"


# ── Schedule a post ─────────────────────────────────────────────────────────

@router.post("/scheduled")
async def create_scheduled_post(payload: SchedulePostPayload, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")

    if payload.platform.lower() not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {payload.platform}")

    when = payload.scheduled_for.astimezone(timezone.utc)
    if when <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="scheduled_for must be in the future")

    try:
        sb = get_supabase()
        row = {
            "tenant_id": tenant_id,
            "platform": payload.platform.lower(),
            "content": payload.content,
            "topic": payload.topic,
            "style": payload.style,
            "status": "scheduled",
            "scheduled_for": when.isoformat(),
            "created_at": _utc_now(),
        }
        result = sb.table("social_posts").insert(row).execute()
        return {"success": True, "post": (result.data or [row])[0]}
    except Exception as e:
        logger.error(f"create_scheduled_post error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── List scheduled posts ────────────────────────────────────────────────────

@router.get("/scheduled")
async def list_scheduled_posts(request: Request, limit: int = 50):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("social_posts")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("status", "scheduled")
            .order("scheduled_for", desc=False)
            .limit(limit)
            .execute()
        )
        return {"posts": result.data or []}
    except Exception as e:
        logger.error(f"list_scheduled_posts error: {e}")
        return {"posts": []}


# ── Reschedule / cancel ─────────────────────────────────────────────────────

@router.patch("/scheduled/{post_id}")
async def update_scheduled_post(post_id: str, payload: ReschedulePayload, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        update: Dict[str, Any] = {}
        if payload.cancel:
            update["status"] = "cancelled"
        if payload.scheduled_for:
            ts = payload.scheduled_for.astimezone(timezone.utc)
            if ts <= datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="scheduled_for must be in the future")
            update["scheduled_for"] = ts.isoformat()
        if payload.content is not None:
            update["content"] = payload.content
        if not update:
            return {"success": True, "message": "Nothing to update"}
        sb.table("social_posts").update(update).eq("id", post_id).eq("tenant_id", tenant_id).execute()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_scheduled_post error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/scheduled/{post_id}")
async def delete_scheduled_post(post_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("social_posts").delete().eq("id", post_id).eq("tenant_id", tenant_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"delete_scheduled_post error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Publish a scheduled post ────────────────────────────────────────────────

async def _publish_to_platform(platform: str, content: str, tenant_id: str) -> Dict[str, Any]:
    """
    Try to publish via the tenant's configured social agent. Falls back to a
    soft-publish (mark as published with no external delivery) when the
    relevant API credentials are missing — useful for trials and demo mode.
    """
    platform = platform.lower()
    try:
        if platform in ("twitter", "x"):
            from shared.tenant_agents import get_social_agent
            agent = await get_social_agent(tenant_id)
            if hasattr(agent, "publish_tweet"):
                result = await agent.publish_tweet(content)
                return {"delivered": True, "external_id": result.get("id")}
        elif platform == "linkedin":
            from shared.tenant_agents import get_linkedin_agent
            agent = await get_linkedin_agent(tenant_id)
            if hasattr(agent, "publish_post"):
                result = await agent.publish_post(content)
                return {"delivered": True, "external_id": result.get("id")}
    except Exception as e:
        logger.warning(f"External publish failed on {platform} for {tenant_id}: {e}")
    return {"delivered": False, "reason": "no_credentials_or_unsupported"}


@router.post("/scheduled/{post_id}/publish")
async def publish_scheduled_post(post_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("social_posts")
            .select("*")
            .eq("id", post_id)
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )
        post = result.data
        if not post:
            raise HTTPException(status_code=404, detail="Scheduled post not found")

        delivery = await _publish_to_platform(post["platform"], post["content"], tenant_id)
        sb.table("social_posts").update(
            {
                "status": "published" if delivery["delivered"] else "published_locally",
                "published_at": _utc_now(),
                "engagement_data": {**(post.get("engagement_data") or {}), **delivery},
            }
        ).eq("id", post_id).execute()
        return {"success": True, "delivery": delivery}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"publish_scheduled_post error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Engagement monitoring ───────────────────────────────────────────────────

@router.get("/engagement")
async def engagement_summary(request: Request, days: int = 30):
    tenant_id = getattr(request.state, "tenant_id", "default")
    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_SOCIAL_POSTS
        posts = DEMO_SOCIAL_POSTS
    else:
        try:
            sb = get_supabase()
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            result = (
                sb.table("social_posts")
                .select("*")
                .eq("tenant_id", tenant_id)
                .gte("created_at", since)
                .execute()
            )
            posts = result.data or []
        except Exception as e:
            logger.error(f"engagement_summary fetch error: {e}")
            posts = []

    by_platform: Dict[str, Dict[str, float]] = {}
    by_day: Dict[str, Dict[str, int]] = {}

    for p in posts:
        plat = (p.get("platform") or "unknown").lower()
        likes = int(p.get("likes") or 0)
        comments = int(p.get("comments") or 0)
        shares = int(p.get("shares") or 0)
        impressions = int(p.get("impressions") or 0)

        bucket = by_platform.setdefault(
            plat, {"posts": 0, "likes": 0, "comments": 0, "shares": 0, "impressions": 0}
        )
        bucket["posts"] += 1
        bucket["likes"] += likes
        bucket["comments"] += comments
        bucket["shares"] += shares
        bucket["impressions"] += impressions

        ts = p.get("published_at") or p.get("created_at")
        if ts:
            day = ts[:10]
            db = by_day.setdefault(day, {"likes": 0, "comments": 0, "shares": 0, "impressions": 0})
            db["likes"] += likes
            db["comments"] += comments
            db["shares"] += shares
            db["impressions"] += impressions

    daily_trend: List[Dict[str, Any]] = [
        {"date": day, **stats} for day, stats in sorted(by_day.items())
    ]

    return {
        "by_platform": by_platform,
        "daily_trend": daily_trend,
        "total_posts": len(posts),
    }


# ── Reply automation ────────────────────────────────────────────────────────

@router.post("/replies/draft")
async def draft_reply(payload: ReplyDraftPayload, request: Request):
    """Generate one AI-drafted reply for a mention. Caller decides to send or not."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        import anthropic

        from shared.tenant import get_tenant_config

        cfg = await get_tenant_config(tenant_id) if tenant_id != "default" else None
        api_key = (cfg.anthropic_api_key if cfg else "") or settings.ANTHROPIC_API_KEY
        brand = cfg.brand_name if cfg else "the brand"

        prompt = f"""You are the social media manager for {brand}. Write ONE short
reply to the following {payload.platform} mention. Keep it under 240 chars,
{payload.tone}, no emojis unless the original used them, no hashtags.

Mention from {payload.author or 'a user'}:
\"\"\"{payload.mention_text}\"\"\"

Return ONLY the reply text, no quotes, no preamble."""

        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip().strip('"')
        return {"reply": text}
    except Exception as e:
        logger.error(f"draft_reply error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
