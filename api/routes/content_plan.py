"""
Content Plan API
================

A persistent plan of content ideas. Each idea lives in
``content_plan_items`` with a status (idea → drafting → draft → published)
and an optional FK to a row in ``content_pieces`` once the idea has been
materialised into a full article.

This is the backend half of the dashboard's "Content Plan" view — the
UI lists every idea, lets the user click into one, expand it to a
draft article, and keep iterating with the AI editor.
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Models ───────────────────────────────────────────────────────────────────

class PlanItemCreate(BaseModel):
    title: str
    topic: Optional[str] = None
    content_type: str = "blog_article"
    target_keyword: Optional[str] = None
    pillar: Optional[str] = None
    reason: Optional[str] = None
    priority: str = "medium"
    status: str = "idea"


class PlanItemUpdate(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None
    content_type: Optional[str] = None
    target_keyword: Optional[str] = None
    pillar: Optional[str] = None
    reason: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    content_piece_id: Optional[str] = None


class PlanGenerateRequest(BaseModel):
    count: int = 6
    brand_name: Optional[str] = None
    domain: Optional[str] = None
    brand_description: Optional[str] = None
    target_audience: Optional[str] = None
    competitors: List[str] = []
    pillar: Optional[str] = None
    replace: bool = False  # if True, archive existing 'idea' rows first


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_brand_context(tenant_id: str) -> dict:
    if not tenant_id or tenant_id == "default":
        return {}
    try:
        sb = get_supabase()
        data = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        return data.data.get("settings", {}) if data.data else {}
    except Exception:
        return {}


def _row(item: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "title": item.get("title") or "",
        "topic": item.get("topic") or "",
        "content_type": item.get("content_type") or "blog_article",
        "target_keyword": item.get("target_keyword") or "",
        "pillar": item.get("pillar") or "",
        "reason": item.get("reason") or "",
        "priority": item.get("priority") or "medium",
        "status": item.get("status") or "idea",
        "content_piece_id": item.get("content_piece_id"),
        "metadata": item.get("metadata") or {},
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("/plan")
async def list_plan(request: Request, status: Optional[str] = None, limit: int = 200):
    """List content plan items for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        q = (
            sb.table("content_plan_items")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            q = q.eq("status", status)
        result = q.execute()
        return {"items": [_row(x) for x in (result.data or [])]}
    except Exception as e:
        logger.error(f"list_plan error: {e}")
        return {"items": [], "error": str(e)}


# ── Create one ───────────────────────────────────────────────────────────────

@router.post("/plan")
async def create_plan_item(request: Request, payload: PlanItemCreate):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        data = {
            **payload.model_dump(exclude_none=True),
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = sb.table("content_plan_items").insert(data).execute()
        return {"success": True, "item": _row(result.data[0]) if result.data else data}
    except Exception as e:
        logger.error(f"create_plan_item error: {e}")
        return {"success": False, "error": str(e)}


# ── Update ───────────────────────────────────────────────────────────────────

@router.patch("/plan/{item_id}")
async def update_plan_item(item_id: str, payload: PlanItemUpdate, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not update_data:
            return {"success": True, "message": "Nothing to update"}
        result = (
            sb.table("content_plan_items")
            .update(update_data)
            .eq("id", item_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        if result.data:
            return {"success": True, "item": _row(result.data[0])}
        return {"success": True}
    except Exception as e:
        logger.error(f"update_plan_item error: {e}")
        return {"success": False, "error": str(e)}


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/plan/{item_id}")
async def delete_plan_item(item_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("content_plan_items").delete().eq("id", item_id).eq("tenant_id", tenant_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"delete_plan_item error: {e}")
        return {"success": False, "error": str(e)}


# ── AI batch generation ──────────────────────────────────────────────────────

@router.post("/plan/generate")
async def generate_plan(request: Request, payload: PlanGenerateRequest):
    """Use Claude to generate N content ideas and persist them as plan items.

    Unlike the existing ``/suggest-topics`` (which just returns a list and
    forgets it), this writes the ideas to ``content_plan_items`` so the
    user's plan grows over time and survives reloads.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")

    # Fill in brand context from user_settings if the caller didn't provide it.
    s = _load_brand_context(tenant_id)
    brand_name = payload.brand_name or s.get("brand_name", "")
    domain = payload.domain or s.get("domain", "")
    brand_description = payload.brand_description or s.get("brand_description", "")
    target_audience = payload.target_audience or s.get("target_audience", "")
    competitors = payload.competitors or s.get("competitors", []) or []

    count = max(1, min(payload.count, 12))

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        pillar_hint = f"\nFocus pillar: {payload.pillar}" if payload.pillar else ""
        prompt = f"""You are a B2B SaaS content strategist. Generate {count} content ideas
the team can publish over the next quarter.

Brand: {brand_name}
Website: {domain}
Description: {brand_description}
Target audience: {target_audience}
Competitors: {', '.join(competitors) if competitors else 'N/A'}{pillar_hint}

Mix the formats roughly: 60% blog_article, 25% linkedin_post, 15% email.

Return ONLY a JSON array (no markdown, no code fences) of {count} objects:
[
  {{
    "title": "Concrete article headline",
    "topic": "What the article is about, one sentence",
    "content_type": "blog_article|linkedin_post|email|comparison",
    "target_keyword": "primary SEO keyword",
    "pillar": "churn_prevention|health_scoring|cs_automation|onboarding|nrr_growth|competitor",
    "priority": "high|medium|low",
    "reason": "Why this matters now, one sentence"
  }}
]
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()

        try:
            ideas = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                fenced = text.split("```")[1]
                if fenced.startswith("json"):
                    fenced = fenced[4:]
                ideas = json.loads(fenced.strip())
            else:
                ideas = []

        if not isinstance(ideas, list):
            return {"success": False, "error": "AI returned non-list payload", "items": []}

        sb = get_supabase()

        if payload.replace:
            try:
                sb.table("content_plan_items").update({"status": "archived"}).eq(
                    "tenant_id", tenant_id
                ).eq("status", "idea").execute()
            except Exception as e:
                logger.debug(f"Failed to archive existing ideas: {e}")

        rows = []
        for raw in ideas:
            if not isinstance(raw, dict):
                continue
            rows.append({
                "tenant_id": tenant_id,
                "title": str(raw.get("title") or "Untitled idea")[:300],
                "topic": str(raw.get("topic") or "")[:1000],
                "content_type": str(raw.get("content_type") or "blog_article"),
                "target_keyword": str(raw.get("target_keyword") or "")[:200],
                "pillar": str(raw.get("pillar") or "")[:100],
                "priority": str(raw.get("priority") or "medium"),
                "reason": str(raw.get("reason") or "")[:500],
                "status": "idea",
                "metadata": {"generator": "claude", "model": settings.CLAUDE_MODEL},
            })

        if not rows:
            return {"success": False, "error": "No usable ideas in AI response", "items": []}

        result = sb.table("content_plan_items").insert(rows).execute()
        return {
            "success": True,
            "items": [_row(x) for x in (result.data or [])],
        }
    except Exception as e:
        logger.error(f"generate_plan error: {e}")
        return {"success": False, "error": str(e), "items": []}


# ── Materialise an idea into a content_piece (drafted full article) ──────────

@router.post("/plan/{item_id}/draft")
async def draft_plan_item(item_id: str, request: Request):
    """Turn a plan item into a full article and link them.

    Generates the article body via Claude, inserts a row in
    ``content_pieces``, and updates the plan item to status='draft' with
    ``content_piece_id`` pointing at the new article. The dashboard then
    routes the user to /content/<piece_id> for editing.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("content_plan_items")
            .select("*")
            .eq("id", item_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Plan item not found")
        item = rows[0]

        # Mark as drafting so concurrent clicks don't duplicate the article.
        sb.table("content_plan_items").update({"status": "drafting"}).eq("id", item_id).execute()

        # Generate the article via Claude.
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        ctype = item.get("content_type") or "blog_article"
        target_words = (
            "1500-2200 words" if ctype == "blog_article" else
            "120-180 words" if ctype == "linkedin_post" else
            "300-500 words"
        )
        prompt = f"""You are an expert B2B SaaS marketer. Write a {ctype.replace('_', ' ')}.

Title: {item.get('title') or ''}
Topic: {item.get('topic') or ''}
Target keyword: {item.get('target_keyword') or ''}
Pillar: {item.get('pillar') or ''}
Length: {target_words}.

Return ONLY a JSON object (no markdown fences):
{{
  "title": "Final title (may refine the input)",
  "content": "Full article in markdown",
  "meta_title": "<= 60 chars",
  "meta_description": "150-160 chars",
  "word_count": <number>
}}
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        try:
            article = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                fenced = text.split("```")[1]
                if fenced.startswith("json"):
                    fenced = fenced[4:]
                article = json.loads(fenced.strip())
            else:
                article = {"title": item.get("title"), "content": text, "word_count": len(text.split())}

        piece_data = {
            "tenant_id": tenant_id,
            "title": article.get("title") or item.get("title") or "Untitled",
            "content": article.get("content") or "",
            "content_type": ctype,
            "meta_title": article.get("meta_title") or "",
            "meta_description": article.get("meta_description") or "",
            "target_keyword": item.get("target_keyword") or "",
            "word_count": int(article.get("word_count") or len((article.get("content") or "").split())),
            "status": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        piece_result = sb.table("content_pieces").insert(piece_data).execute()
        piece = (piece_result.data or [{}])[0]
        piece_id = piece.get("id")

        # Link the plan item back to the article.
        sb.table("content_plan_items").update({
            "status": "draft",
            "content_piece_id": piece_id,
        }).eq("id", item_id).execute()

        return {
            "success": True,
            "plan_item_id": item_id,
            "content_piece_id": piece_id,
            "piece": piece,
        }
    except HTTPException:
        # Roll the status back so the user can retry.
        try:
            sb.table("content_plan_items").update({"status": "idea"}).eq("id", item_id).execute()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"draft_plan_item error: {e}")
        try:
            sb.table("content_plan_items").update({"status": "idea"}).eq("id", item_id).execute()
        except Exception:
            pass
        return {"success": False, "error": str(e)}
