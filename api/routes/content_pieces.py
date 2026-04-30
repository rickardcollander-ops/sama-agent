"""
Content Pieces API Routes
CRUD for content pieces (blog articles, landing pages, etc.) scoped by tenant.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase
from shared.usage import UsageLimitExceeded, check_and_increment

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


class ContentPieceUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keyword: Optional[str] = None
    target_url: Optional[str] = None
    word_count: Optional[int] = None
    status: Optional[str] = None


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
    try:
        await check_and_increment(tenant_id, "content_pieces")
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
        data = {
            **payload.model_dump(),
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = sb.table("content_pieces").insert(data).execute()
        return {"success": True, "piece": _ensure_numeric(result.data[0]) if result.data else data}
    except Exception as e:
        logger.error(f"create_content_piece error: {e}")
        return {"success": False, "error": str(e)}


# ── Update ───────────────────────────────────────────────────────────────────

@router.patch("/pieces/{piece_id}")
async def update_content_piece(piece_id: str, payload: ContentPieceUpdate):
    """Update an existing content piece."""
    try:
        sb = get_supabase()
        update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not update_data:
            return {"success": True, "message": "Nothing to update"}
        result = sb.table("content_pieces").update(update_data).eq("id", piece_id).execute()
        if result.data:
            return {"success": True, "piece": _ensure_numeric(result.data[0])}
        return {"success": True, "message": "Updated"}
    except Exception as e:
        logger.error(f"update_content_piece error: {e}")
        return {"success": False, "error": str(e)}


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/pieces/{piece_id}")
async def delete_content_piece(piece_id: str):
    """Archive/delete a content piece."""
    try:
        sb = get_supabase()
        sb.table("content_pieces").delete().eq("id", piece_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"delete_content_piece error: {e}")
        return {"success": False, "error": str(e)}
