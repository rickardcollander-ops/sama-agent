"""
AI editor endpoints for ``content_pieces``.

Two operations the dashboard's article editor relies on:

* ``POST /api/content/pieces/{id}/ai-edit``     — apply an instruction to the
  existing article (e.g. "make it shorter", "add a CTA at the end",
  "translate to Swedish"). Optionally scoped to a selection.
* ``POST /api/content/pieces/{id}/ai-rewrite``  — throw the current draft
  away and write a fresh one from a new brief.

Both write the result back to the piece, so the UI just needs to
re-fetch the article after the call returns.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class AIEditRequest(BaseModel):
    instruction: str
    # Optional: only rewrite this slice of the content. The frontend sends
    # the highlighted text; we replace it in-place with the AI output.
    selection: Optional[str] = None
    tone: Optional[str] = None
    language: Optional[str] = None  # ISO code, e.g. "sv"


class AIRewriteRequest(BaseModel):
    brief: str
    tone: Optional[str] = None
    target_keyword: Optional[str] = None
    word_count: Optional[int] = None
    language: Optional[str] = None


def _get_piece(sb, piece_id: str, tenant_id: str) -> dict:
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
        raise HTTPException(status_code=404, detail="Content piece not found")
    return rows[0]


def _parse_json_or_fenced(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if "```" in text:
            fenced = text.split("```")[1]
            if fenced.startswith("json"):
                fenced = fenced[4:]
            return json.loads(fenced.strip())
        raise


@router.post("/pieces/{piece_id}/ai-edit")
async def ai_edit_piece(piece_id: str, payload: AIEditRequest, request: Request):
    """Apply an AI instruction to an existing article."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        piece = _get_piece(sb, piece_id, tenant_id)

        original = piece.get("content") or ""
        title = piece.get("title") or ""
        keyword = piece.get("target_keyword") or ""

        # Selection-scoped edit: rewrite the selection only and splice it
        # back into the original. Falls back to whole-document edit when
        # the selection isn't a literal substring of the content.
        scope = "whole article"
        target_text = original
        if payload.selection and payload.selection in original:
            scope = "highlighted selection"
            target_text = payload.selection

        instruction = payload.instruction.strip() or "Improve clarity and flow"
        tone = (payload.tone or "professional").strip()
        language = (payload.language or "").strip()
        lang_line = f"\nWrite the output in language: {language}." if language else ""

        prompt = f"""You are an expert B2B SaaS editor. Apply the following instruction to the {scope}.

Instruction: {instruction}
Article title: {title}
Target keyword: {keyword}
Tone: {tone}{lang_line}

INPUT (the {scope} to edit):
---
{target_text}
---

Return ONLY a JSON object (no markdown fences):
{{
  "content": "The edited markdown for the {scope}",
  "summary_of_changes": "One sentence describing what you changed"
}}
"""
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        try:
            edited = _parse_json_or_fenced(text)
        except Exception:
            # Last-ditch fallback: treat the whole response as the new content.
            edited = {"content": text.strip(), "summary_of_changes": "AI returned non-JSON; using raw output."}

        new_segment = (edited.get("content") or "").strip()
        if not new_segment:
            return {"success": False, "error": "AI returned empty content"}

        if scope == "highlighted selection":
            new_content = original.replace(target_text, new_segment, 1)
        else:
            new_content = new_segment

        update = {
            "content": new_content,
            "word_count": len(new_content.split()),
        }
        sb.table("content_pieces").update(update).eq("id", piece_id).execute()

        return {
            "success": True,
            "piece_id": piece_id,
            "content": new_content,
            "summary_of_changes": edited.get("summary_of_changes") or "",
            "scope": scope,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ai_edit_piece error: {e}")
        return {"success": False, "error": str(e)}


@router.post("/pieces/{piece_id}/ai-rewrite")
async def ai_rewrite_piece(piece_id: str, payload: AIRewriteRequest, request: Request):
    """Rewrite an article from scratch using a new brief."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        piece = _get_piece(sb, piece_id, tenant_id)

        ctype = piece.get("content_type") or "blog_article"
        target_kw = payload.target_keyword or piece.get("target_keyword") or ""
        target_words = payload.word_count or (
            2000 if ctype == "blog_article" else
            150 if ctype == "linkedin_post" else
            400
        )
        tone = (payload.tone or "professional").strip()
        language = (payload.language or "").strip()
        lang_line = f"\nWrite the output in language: {language}." if language else ""

        prompt = f"""You are an expert B2B SaaS marketer. Rewrite this {ctype.replace('_', ' ')}
from scratch using the brief below. Discard the previous draft entirely.

Brief: {payload.brief.strip()}
Title hint: {piece.get('title') or ''}
Target keyword: {target_kw}
Tone: {tone}
Target length: about {target_words} words.{lang_line}

Return ONLY a JSON object (no markdown fences):
{{
  "title": "Refined title",
  "content": "Full markdown article",
  "meta_title": "<= 60 chars",
  "meta_description": "150-160 chars",
  "word_count": <number>
}}
"""
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        try:
            article = _parse_json_or_fenced(text)
        except Exception:
            article = {"title": piece.get("title"), "content": text.strip()}

        new_content = (article.get("content") or "").strip()
        if not new_content:
            return {"success": False, "error": "AI returned empty content"}

        update = {
            "title": article.get("title") or piece.get("title"),
            "content": new_content,
            "meta_title": article.get("meta_title") or piece.get("meta_title") or "",
            "meta_description": article.get("meta_description") or piece.get("meta_description") or "",
            "target_keyword": target_kw,
            "word_count": int(article.get("word_count") or len(new_content.split())),
            "status": "draft",
        }
        sb.table("content_pieces").update(update).eq("id", piece_id).execute()

        return {
            "success": True,
            "piece_id": piece_id,
            "title": update["title"],
            "content": new_content,
            "meta_description": update["meta_description"],
            "word_count": update["word_count"],
            "rewrote_at": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ai_rewrite_piece error: {e}")
        return {"success": False, "error": str(e)}
