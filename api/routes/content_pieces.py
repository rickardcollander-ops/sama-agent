"""
Content Pieces API Routes
CRUD for content pieces (blog articles, landing pages, etc.) scoped by tenant.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
    # Sprint 2 (K-2 / K-5) — links back to the surface that motivated this
    # article. At most one is set: a gap from Insikter, or a strategy topic.
    source_gap_id: Optional[str] = None
    source_gap_title: Optional[str] = None
    source_strategy_topic: Optional[str] = None


class ContentPieceUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keyword: Optional[str] = None
    target_url: Optional[str] = None
    word_count: Optional[int] = None
    status: Optional[str] = None
    source_gap_id: Optional[str] = None
    source_gap_title: Optional[str] = None
    source_strategy_topic: Optional[str] = None


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
        # Drop None values so optional columns added in later migrations
        # (e.g. 032_content_source_links) don't break inserts in
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


# ── Publish ──────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-")


@router.post("/pieces/{piece_id}/publish")
async def publish_content_piece(piece_id: str, request: Request):
    """Publish a content piece by raising a GitHub PR + flipping status.

    The dashboard's editor calls this from the "Approve & Publish" button.
    Steps:
    1. Load the piece, ensure tenant ownership and status != published.
    2. Pick the right helper based on content_type
       (create_comparison_page_pr / create_blog_post_pr).
    3. On success, set status='published' and target_url, and propagate
       status='published' to any linked plan_item.
    4. Publish a content_published event so the social agent can promote it.
    """
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
            return {"success": False, "error": "Piece not found"}
        piece = rows[0]

        if piece.get("status") == "published":
            return {"success": True, "already_published": True, "target_url": piece.get("target_url")}

        title = piece.get("title") or "Untitled"
        content = piece.get("content") or ""
        ctype = piece.get("content_type") or "blog_article"
        keyword = piece.get("target_keyword") or ""
        meta_description = piece.get("meta_description") or ""

        github_result: Dict[str, Any]

        if ctype == "comparison":
            # Try to extract a competitor name from the title — same convention
            # the existing execute() flow uses ("Successifier vs <competitor>").
            from shared.github_helper import create_comparison_page_pr
            m = re.search(r"vs\s+([A-Za-z0-9_\- ]+)", title)
            competitor = (m.group(1).strip().lower().split()[0] if m else (keyword or "competitor"))
            github_result = await create_comparison_page_pr(competitor=competitor, content=content)
            slug = competitor.replace(" ", "-")
            target_url = f"https://successifier.com/vs/{slug}"
        else:
            from shared.github_helper import create_blog_post_pr
            slug = _slugify(title)
            github_result = await create_blog_post_pr(
                title=title,
                content=content,
                slug=slug,
                excerpt=meta_description[:160],
                keywords=[keyword] if keyword else [],
                meta_description=meta_description,
                author="SAMA Content Agent",
            )
            target_url = f"https://successifier.com/blog/{slug}"

        if not github_result.get("success"):
            return {"success": False, "error": github_result.get("error") or "GitHub publish failed", "github": github_result}

        # Reflect the new state.
        sb.table("content_pieces").update({
            "status": "published",
            "target_url": target_url,
        }).eq("id", piece_id).execute()

        # Mark any linked plan item as published too.
        try:
            sb.table("content_plan_items").update({"status": "published"}).eq(
                "content_piece_id", piece_id
            ).execute()
        except Exception as e:
            logger.debug(f"Failed to update plan_item status: {e}")

        # Mark any pending_approval row for this piece as published.
        try:
            sb.table("pending_approvals").update({
                "status": "published",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }).contains("metadata", {"piece_id": piece_id}).execute()
        except Exception as e:
            logger.debug(f"Failed to update pending_approvals: {e}")

        # Promote on social channels.
        try:
            from shared.event_bus_registry import get_event_bus
            bus = get_event_bus()
            if bus:
                await bus.publish("content_published", "sama_social", {
                    "title": title,
                    "url": target_url,
                    "type": "comparison" if ctype == "comparison" else "blog_post",
                    "keyword": keyword,
                    "pr_url": github_result.get("pr_url", ""),
                })
        except Exception as e:
            logger.debug(f"Failed to publish content_published event: {e}")

        return {
            "success": True,
            "piece_id": piece_id,
            "target_url": target_url,
            "pr_url": github_result.get("pr_url", ""),
            "github": github_result,
        }
    except Exception as e:
        logger.error(f"publish_content_piece error: {e}")
        return {"success": False, "error": str(e)}
