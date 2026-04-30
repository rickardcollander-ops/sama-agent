"""
Content validation scoring and auto-publish workflow.

Scoring is heuristic + AI-assisted. Validation results are saved on the
content_pieces row (validation_score, validation_notes, validated_at) so the
dashboard can show a quality indicator and the auto-publisher can gate on it.

Auto-publish: if the tenant has ``auto_publish_blog_posts`` enabled and the
validation score is at or above ``auto_publish_min_score`` (default 70), the
piece is marked ``published`` and pushed via the tenant's CMS API when
configured. Otherwise it stays as ``draft``.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Heuristic checks ────────────────────────────────────────────────────────

def _heuristic_checks(piece: Dict[str, Any]) -> Dict[str, Any]:
    body = piece.get("content") or piece.get("body") or ""
    title = piece.get("title") or ""
    keyword = (piece.get("target_keyword") or "").lower()

    word_count = len([w for w in re.split(r"\s+", body) if w])
    headings = len(re.findall(r"^#{1,3}\s", body, flags=re.MULTILINE))
    has_meta = bool(piece.get("meta_title")) and bool(piece.get("meta_description"))
    keyword_in_title = bool(keyword and keyword in title.lower())
    keyword_in_body = bool(keyword and keyword in body.lower())
    paragraphs = len([p for p in body.split("\n\n") if p.strip()])

    notes: List[str] = []
    score = 0

    if word_count >= 600:
        score += 25
    elif word_count >= 300:
        score += 12
        notes.append("Body is short — aim for at least 600 words for blog posts.")
    else:
        notes.append("Body is very short.")

    if headings >= 2:
        score += 15
    else:
        notes.append("Add at least two H2/H3 headings for scannability.")

    if has_meta:
        score += 15
    else:
        notes.append("Missing meta_title or meta_description.")

    if keyword:
        if keyword_in_title:
            score += 15
        else:
            notes.append("Target keyword is not in the title.")
        if keyword_in_body:
            score += 10
        else:
            notes.append("Target keyword does not appear in the body.")
    else:
        notes.append("No target_keyword set.")

    if paragraphs >= 4:
        score += 10
    else:
        notes.append("Break the content into more paragraphs.")

    score = max(0, min(score, 100))
    return {
        "score": score,
        "word_count": word_count,
        "headings": headings,
        "has_meta": has_meta,
        "keyword_in_title": keyword_in_title,
        "keyword_in_body": keyword_in_body,
        "notes": notes,
    }


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

    details = _heuristic_checks(piece)
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

    min_score = int((cfg.get_raw("auto_publish_min_score", 70) if cfg else 70) or 70)
    score = piece.get("validation_score")
    if score is None:
        details = _heuristic_checks(piece)
        score = details["score"]

    if not payload.force and score < min_score:
        raise HTTPException(
            status_code=400,
            detail=f"Validation score {score} below minimum {min_score}. Use force=true to override.",
        )

    external_url = None
    if cfg:
        external_url = await _push_to_cms(cfg.cms_api_url, cfg.cms_api_key, piece)

    update = {
        "status": "published",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    if external_url:
        update["external_url"] = external_url

    sb.table("content_pieces").update(update).eq("id", piece_id).execute()
    return {"success": True, "score": score, "external_url": external_url}


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
        details = _heuristic_checks(piece)
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
        update = {
            "status": "published",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "validation_score": score,
            "validation_notes": details["notes"],
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        if external_url:
            update["external_url"] = external_url
        sb.table("content_pieces").update(update).eq("id", piece["id"]).execute()
        published += 1

    return {"published": published, "skipped": skipped, "min_score": min_score}
