"""
Single publishing pipeline for content pieces.

Every code path that flips a piece to "published" — the manual publish
endpoint, the autopilot draft phase, the scheduled due-date publish and the
auto-publish sweep — goes through this module, so the resulting row state is
identical regardless of which path shipped the article: status, published_at,
external_url/target_url, validation_score, the linked content_plan_items row
and any pending_approvals row.

Lives in shared/ (not api/routes/) so both the scheduler and the route
handlers can import it at top level without circular-import workarounds.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-")


# ── Heuristic quality scoring ────────────────────────────────────────────────

def heuristic_checks(piece: Dict[str, Any]) -> Dict[str, Any]:
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


# ── GitHub publish ───────────────────────────────────────────────────────────

async def publish_via_github(piece: Dict[str, Any]) -> Dict[str, Any]:
    """Raise a GitHub PR with the article. Returns dict with success + pr_url + url."""
    title = piece.get("title") or "Untitled"
    content = piece.get("content") or ""
    keyword = piece.get("target_keyword") or ""
    meta_description = piece.get("meta_description") or ""
    ctype = piece.get("content_type") or "blog_article"

    if ctype == "comparison":
        from shared.github_helper import create_comparison_page_pr
        m = re.search(r"vs\s+([A-Za-z0-9_\- ]+)", title)
        competitor = (m.group(1).strip().lower().split()[0] if m else (keyword or "competitor"))
        result = await create_comparison_page_pr(competitor=competitor, content=content)
        url = f"https://successifier.com/vs/{competitor.replace(' ', '-')}"
    else:
        from shared.github_helper import create_blog_post_pr
        slug = slugify(title)
        result = await create_blog_post_pr(
            title=title,
            content=content,
            slug=slug,
            excerpt=meta_description[:160],
            keywords=[keyword] if keyword else [],
            meta_description=meta_description,
            author="SAMA Content Agent",
        )
        url = f"https://successifier.com/blog/{slug}"

    if result.get("success"):
        result["url"] = url
    return result


# ── Post-publish state sync ──────────────────────────────────────────────────

async def finalize_published_piece(
    sb,
    piece_id: str,
    *,
    tenant_id: Optional[str] = None,
    url: Optional[str] = None,
    score: Optional[int] = None,
    plan_item_id: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> None:
    """Flip a piece to published and sync every row that mirrors its state.

    Updates content_pieces (status, published_at, external_url/target_url,
    validation_score), the linked content_plan_items row (by ``plan_item_id``
    when the caller has it, otherwise by the content_piece_id FK) and any
    pending_approvals row referencing the piece. Plan/approval sync is
    best-effort: a failure there must not roll back an already-live article.
    """
    update: Dict[str, Any] = {
        "status": "published",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    if url:
        update["external_url"] = url
        update["target_url"] = url
    if score is not None:
        update["validation_score"] = int(score)
    if extra_fields:
        update.update(extra_fields)

    q = sb.table("content_pieces").update(update).eq("id", piece_id)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    q.execute()

    try:
        plan_q = sb.table("content_plan_items").update({"status": "published"})
        if plan_item_id:
            plan_q = plan_q.eq("id", plan_item_id)
        else:
            plan_q = plan_q.eq("content_piece_id", piece_id)
        plan_q.execute()
    except Exception as e:
        logger.debug(f"finalize_published_piece: plan_item sync failed for {piece_id}: {e}")

    try:
        sb.table("pending_approvals").update({
            "status": "published",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }).contains("metadata", {"piece_id": piece_id}).execute()
    except Exception as e:
        logger.debug(f"finalize_published_piece: approval sync failed for {piece_id}: {e}")
