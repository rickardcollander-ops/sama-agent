"""
Content validation scoring and auto-publish workflow.

Scoring is heuristic + AI-assisted. Validation results are saved on the
content_pieces row (validation_score, validation_notes, validated_at) so the
dashboard can show a quality indicator and the auto-publisher can gate on it.

Auto-publish: if the tenant has ``auto_publish_blog_posts`` enabled and the
validation score is at or above ``auto_publish_min_score`` (default 70), the
piece is marked ``published`` and either pushed to the tenant's CMS (when
``cms_api_url`` is set) or shipped via a GitHub Pull Request through the
``shared.github_helper`` flow used by the rest of the agent. Otherwise the
piece stays as ``draft`` and the editor's "Approve & Publish" button is
disabled until the score crosses the threshold.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase
from shared.publishing import (
    finalize_published_piece,
    heuristic_checks,
    publish_via_github,
)
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


# ── /pieces/{id}/validate ───────────────────────────────────────────────────


class ValidateResponse(BaseModel):
    score: int
    notes: List[str]
    details: Dict[str, Any]


@router.post("/pieces/{piece_id}/validate", response_model=ValidateResponse)
async def validate_piece(piece_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        res = (
            sb.table("content_pieces")
            .select("*")
            .eq("id", piece_id)
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )
        piece = res.data
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Piece not found: {e}")

    if not piece:
        raise HTTPException(status_code=404, detail="Piece not found")

    details = heuristic_checks(piece)
    score = details["score"]

    try:
        sb.table("content_pieces").update(
            {
                "validation_score": score,
                "validation_notes": details["notes"],
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", piece_id).execute()
    except Exception as e:
        logger.debug(f"Could not persist validation result: {e}")

    return ValidateResponse(score=score, notes=details["notes"], details=details)


# ── /pieces/{id}/publish — manual or auto-publish ───────────────────────────


class PublishPayload(BaseModel):
    force: bool = False  # Skip the score gate
    via: Optional[str] = None  # "cms" | "github" — default: prefer CMS, fall back to GitHub


async def _push_to_cms(cms_api_url: str, cms_api_key: str, piece: Dict[str, Any]) -> Optional[str]:
    """Best-effort POST to the tenant's CMS. Returns external URL on success."""
    if not cms_api_url:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{cms_api_url.rstrip('/')}/posts",
                headers={"Authorization": f"Bearer {cms_api_key}"} if cms_api_key else {},
                json={
                    "title": piece.get("title"),
                    "content": piece.get("content"),
                    "meta_title": piece.get("meta_title"),
                    "meta_description": piece.get("meta_description"),
                    "slug": piece.get("slug"),
                },
            )
            if res.status_code in (200, 201):
                return (res.json() or {}).get("url")
            logger.warning(f"CMS push failed: {res.status_code}")
    except Exception as e:
        logger.warning(f"CMS push exception: {e}")
    return None


@router.post("/pieces/{piece_id}/publish")
async def publish_piece(piece_id: str, payload: PublishPayload, request: Request):
    """Publish a content piece — gates on validation score, then ships.

    Shipping path resolution:
    1. If ``payload.via == 'github'`` → always GitHub PR.
    2. Else, if tenant has ``cms_api_url`` configured → CMS push.
    3. Otherwise → GitHub PR fallback (so single-tenant default-mode works
       out of the box without per-tenant CMS configuration).
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    cfg = await get_tenant_config(tenant_id) if tenant_id != "default" else None

    sb = get_supabase()
    try:
        res = (
            sb.table("content_pieces")
            .select("*")
            .eq("id", piece_id)
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )
        piece = res.data
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    if not piece:
        raise HTTPException(status_code=404, detail="Piece not found")

    if piece.get("status") == "published":
        return {
            "success": True,
            "already_published": True,
            "external_url": piece.get("external_url") or piece.get("target_url"),
        }

    min_score = int((cfg.get_raw("auto_publish_min_score", 70) if cfg else 70) or 70)
    score = piece.get("validation_score")
    if score is None:
        details = heuristic_checks(piece)
        score = details["score"]

    if not payload.force and score < min_score:
        raise HTTPException(
            status_code=400,
            detail=f"Validation score {score} below minimum {min_score}. Use force=true to override.",
        )

    cms_url = cfg.cms_api_url if cfg else None
    via = payload.via or ("cms" if cms_url else "github")

    external_url: Optional[str] = None
    pr_url: Optional[str] = None
    github_result: Optional[Dict[str, Any]] = None

    if via == "cms" and cms_url:
        external_url = await _push_to_cms(cms_url, cfg.cms_api_key if cfg else "", piece)
    else:
        github_result = await publish_via_github(piece)
        if not github_result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=github_result.get("error") or "GitHub publish failed",
            )
        external_url = github_result.get("url")
        pr_url = github_result.get("pr_url")

    await finalize_published_piece(
        sb, piece_id, tenant_id=tenant_id, url=external_url, score=score
    )

    # Promote on social channels.
    try:
        from shared.event_bus_registry import get_event_bus
        bus = get_event_bus()
        if bus:
            await bus.publish("content_published", "sama_social", {
                "title": piece.get("title", ""),
                "url": external_url or "",
                "type": "comparison" if piece.get("content_type") == "comparison" else "blog_post",
                "keyword": piece.get("target_keyword", ""),
                "pr_url": pr_url or "",
            })
    except Exception as e:
        logger.debug(f"Failed to publish content_published event: {e}")

    return {
        "success": True,
        "score": score,
        "external_url": external_url,
        "pr_url": pr_url,
        "via": via,
    }


# ── /auto-publish — sweep all draft pieces and publish those that qualify ───

@router.post("/auto-publish")
async def auto_publish(request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    if tenant_id == "default":
        raise HTTPException(status_code=400, detail="Tenant ID required")
    cfg = await get_tenant_config(tenant_id)
    if not cfg.auto_publish_blog_posts:
        return {"published": 0, "skipped": 0, "reason": "auto_publish_blog_posts disabled"}

    min_score = int(cfg.get_raw("auto_publish_min_score", 70) or 70)

    sb = get_supabase()
    try:
        result = (
            sb.table("content_pieces")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("status", "draft")
            .limit(20)
            .execute()
        )
        drafts = result.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    published = 0
    skipped = 0
    for piece in drafts:
        details = heuristic_checks(piece)
        score = details["score"]
        if score < min_score:
            sb.table("content_pieces").update(
                {
                    "validation_score": score,
                    "validation_notes": details["notes"],
                    "validated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", piece["id"]).execute()
            skipped += 1
            continue

        external_url = await _push_to_cms(cfg.cms_api_url, cfg.cms_api_key, piece)
        await finalize_published_piece(
            sb,
            piece["id"],
            tenant_id=tenant_id,
            url=external_url,
            score=score,
            extra_fields={
                "validation_notes": details["notes"],
                "validated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        published += 1

    return {"published": published, "skipped": skipped, "min_score": min_score}
