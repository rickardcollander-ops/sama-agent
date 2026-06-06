"""
Content Pieces API Routes
CRUD for content pieces (blog articles, landing pages, etc.) scoped by tenant.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase
from shared.usage import UsageLimitExceeded, SubscriptionRequired, check_and_increment, increment_usage

router = APIRouter()
logger = logging.getLogger(__name__)


class ContentPieceCreate(BaseModel):
    title: str
    content_type: str = "blog_article"
    content: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keyword: Optional[str] = None
    target_url: Optional[str] = None
    word_count: int = 0
    status: str = "draft"
    # Sprint 2 (K-2 / K-5) — links back to the surface that motivated this
    # article. At most one is set: a gap from Insikter, or a strategy topic.
    source_gap_id: Optional[str] = None
    source_gap_title: Optional[str] = None
    source_strategy_topic: Optional[str] = None
    # Premium article fields (set by article_writer; nullable for legacy).
    slug: Optional[str] = None
    featured_image_url: Optional[str] = None
    featured_image_alt: Optional[str] = None
    article_score: Optional[int] = None
    article_data: Optional[Dict[str, Any]] = None
    source: Optional[str] = None


class ContentPieceUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keyword: Optional[str] = None
    target_url: Optional[str] = None
    external_url: Optional[str] = None
    published_at: Optional[str] = None
    word_count: Optional[int] = None
    status: Optional[str] = None
    source_gap_id: Optional[str] = None
    source_gap_title: Optional[str] = None
    source_strategy_topic: Optional[str] = None
    slug: Optional[str] = None
    featured_image_url: Optional[str] = None
    featured_image_alt: Optional[str] = None
    article_score: Optional[int] = None
    article_data: Optional[Dict[str, Any]] = None


def _ensure_numeric(row: dict) -> dict:
    """Replace null numeric fields with 0."""
    for key in ("impressions_30d", "clicks_30d", "word_count"):
        if row.get(key) is None:
            row[key] = 0
    if row.get("avg_position") is None:
        row["avg_position"] = 0.0
    return row


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("/pieces")
async def list_content_pieces(request: Request, limit: int = 100):
    """List content pieces for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    # Demo mode
    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_CONTENT_PIECES
        return {"pieces": DEMO_CONTENT_PIECES}

    try:
        sb = get_supabase()
        result = (
            sb.table("content_pieces")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        pieces = [_ensure_numeric(r) for r in (result.data or [])]
        return {"pieces": pieces}
    except Exception as e:
        logger.error(f"list_content_pieces error: {e}")
        return {"pieces": []}


# ── Create ───────────────────────────────────────────────────────────────────

@router.post("/pieces")
async def create_content_piece(request: Request, payload: ContentPieceCreate):
    """Create a new content piece."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    is_onboarding = payload.source == "onboarding"
    if is_onboarding:
        # Onboarding drafts must always land regardless of trial/subscription
        # state — blocking here would leave new users with an empty content
        # list after completing setup. Still increment best-effort so the
        # usage counter stays accurate once they subscribe.
        try:
            await increment_usage(tenant_id, "content_pieces")
        except Exception:
            pass
    else:
        try:
            await check_and_increment(tenant_id, "content_pieces")
        except SubscriptionRequired as e:
            return {
                "success": False,
                "error": str(e),
                "subscription_required": True,
                "reason": e.reason,
            }
        except UsageLimitExceeded as e:
            return {
                "success": False,
                "error": str(e),
                "limit_exceeded": True,
                "metric": e.metric,
                "limit": e.limit,
            }
    try:
        sb = get_supabase()
        # Drop None values so optional columns added in later migrations
        # (e.g. 041_content_source_links) don't break inserts in
        # environments where the migration hasn't been applied yet.
        payload_dict = {k: v for k, v in payload.model_dump().items() if v is not None}
        data = {
            **payload_dict,
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = sb.table("content_pieces").insert(data).execute()
        return {"success": True, "piece": _ensure_numeric(result.data[0]) if result.data else data}
    except Exception as e:
        logger.error(f"create_content_piece error: {e}")
        return {"success": False, "error": str(e)}


# ── Get single ───────────────────────────────────────────────────────────────

@router.get("/pieces/{piece_id}")
async def get_content_piece(piece_id: str, request: Request):
    """Fetch a single content piece (used by edit/refine flows)."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("content_pieces")
            .select("*")
            .eq("id", piece_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return {"piece": None, "error": "not_found"}
        return {"piece": _ensure_numeric(rows[0])}
    except Exception as e:
        logger.error(f"get_content_piece error: {e}")
        return {"piece": None, "error": str(e)}


# ── Update ───────────────────────────────────────────────────────────────────

@router.patch("/pieces/{piece_id}")
async def update_content_piece(piece_id: str, payload: ContentPieceUpdate):
    """Update an existing content piece."""
    try:
        sb = get_supabase()
        update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not update_data:
            return {"success": True, "message": "Nothing to update"}
        # When a piece flips to "published" (manual publish from the dashboard or
        # the auto-publish bridge), stamp published_at if the caller didn't. The
        # 24h social-promo email keys off this timestamp, so without it that
        # follow-up never fires.
        if update_data.get("status") == "published" and not update_data.get("published_at"):
            update_data["published_at"] = datetime.now(timezone.utc).isoformat()
        result = sb.table("content_pieces").update(update_data).eq("id", piece_id).execute()
        if result.data:
            return {"success": True, "piece": _ensure_numeric(result.data[0])}
        return {"success": True, "message": "Updated"}
    except Exception as e:
        logger.error(f"update_content_piece error: {e}")
        return {"success": False, "error": str(e)}


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/pieces/archived")
async def delete_archived_content_pieces(request: Request):
    """Permanently delete every archived content piece for the current tenant.

    Backs the dashboard's "Töm arkiv" button. Declared before the
    parameterised ``/pieces/{piece_id}`` route so FastAPI matches the
    literal ``archived`` segment first.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("content_pieces")
            .delete()
            .eq("tenant_id", tenant_id)
            .eq("status", "archived")
            .execute()
        )
        return {"success": True, "deleted": len(result.data or [])}
    except Exception as e:
        logger.error(f"delete_archived_content_pieces error: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/pieces/{piece_id}")
async def delete_content_piece(piece_id: str, request: Request):
    """Permanently delete a single content piece (tenant-scoped)."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("content_pieces").delete().eq("id", piece_id).eq("tenant_id", tenant_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"delete_content_piece error: {e}")
        return {"success": False, "error": str(e)}
