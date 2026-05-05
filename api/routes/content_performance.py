"""
Content piece performance + refinement endpoints.

C6 — performance: traffic, ranking and AI mentions for a single piece so we
can answer "did this content actually do something?" inline on the Content
page.

C5 — refine: take a section of text + an intent (grammar / tone / seo) and
return Claude's improved version so the editor can offer one-click rewrites.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


# ── GET /pieces/{id}/performance ─────────────────────────────────────────────


def _piece_keywords(piece: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    candidates = [
        piece.get("target_keyword"),
        piece.get("title"),
    ]
    for c in candidates:
        if not c or not isinstance(c, str):
            continue
        c = c.strip()
        if not c:
            continue
        # Always include the full string and the head term (first 4 words).
        head = " ".join(c.split()[:4])
        for v in (c, head):
            v = v.strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
    return out[:5]


@router.get("/pieces/{piece_id}/performance")
async def piece_performance(piece_id: str, request: Request) -> Dict[str, Any]:
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    try:
        piece_res = (
            sb.table("content_pieces")
            .select("*")
            .eq("id", piece_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"piece_performance load failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load piece")

    rows = piece_res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Content piece not found")
    piece = rows[0]
    keywords = _piece_keywords(piece)

    # ── SEO performance ────────────────────────────────────────────────────
    # The seo_keywords table stores values in `current_*` columns; we
    # normalise to the names the dashboard renders.
    seo: Dict[str, Any] = {"keywords": []}
    try:
        for kw in keywords:
            kw_res = (
                sb.table("seo_keywords")
                .select("keyword,current_position,current_clicks,current_impressions,current_ctr")
                .eq("tenant_id", tenant_id)
                .ilike("keyword", f"%{kw}%")
                .limit(5)
                .execute()
            )
            for row in (kw_res.data or []):
                seo["keywords"].append({
                    "keyword": row.get("keyword"),
                    "position": row.get("current_position"),
                    "clicks": row.get("current_clicks"),
                    "impressions": row.get("current_impressions"),
                    "ctr": row.get("current_ctr"),
                })
        # Dedupe by keyword
        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for k in seo["keywords"]:
            key = (k.get("keyword") or "").lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(k)
        seo["keywords"] = deduped[:10]
        if seo["keywords"]:
            positions = [k.get("position") for k in seo["keywords"] if k.get("position") and k["position"] > 0]
            if positions:
                seo["avg_position"] = round(sum(positions) / len(positions), 1)
            seo["total_clicks"] = sum(k.get("clicks") or 0 for k in seo["keywords"])
            seo["total_impressions"] = sum(k.get("impressions") or 0 for k in seo["keywords"])
    except Exception as e:
        logger.warning(f"piece_performance seo lookup failed: {e}")

    # ── AI mentions ────────────────────────────────────────────────────────
    ai: Dict[str, Any] = {"checks": [], "mention_rate": None}
    try:
        title = piece.get("title") or ""
        head = " ".join(title.split()[:5])
        if head:
            ai_res = (
                sb.table("ai_visibility_checks")
                .select("prompt,mentioned,ai_engine,checked_at")
                .eq("tenant_id", tenant_id)
                .ilike("prompt", f"%{head}%")
                .order("checked_at", desc=True)
                .limit(20)
                .execute()
            )
            ai["checks"] = ai_res.data or []
            if ai["checks"]:
                mentioned = sum(1 for c in ai["checks"] if c.get("mentioned"))
                ai["mention_rate"] = round(mentioned / len(ai["checks"]), 3)
    except Exception as e:
        logger.warning(f"piece_performance ai lookup failed: {e}")

    # ── Traffic (best-effort: read piece's own counters) ───────────────────
    traffic = {
        "clicks_30d": piece.get("clicks_30d") or 0,
        "impressions_30d": piece.get("impressions_30d") or 0,
    }

    # ── Verdict heuristic ──────────────────────────────────────────────────
    avg_pos = seo.get("avg_position")
    mention_rate = ai.get("mention_rate")
    score = 0
    if avg_pos is not None:
        score += 1 if avg_pos <= 15 else -1
    if mention_rate is not None:
        score += 1 if mention_rate >= 0.4 else -1
    if (traffic["clicks_30d"] or 0) > 50:
        score += 1
    verdict = "winning" if score >= 2 else "lagging" if score <= -1 else "mixed" if (avg_pos or mention_rate or traffic["clicks_30d"]) else "untracked"

    return {
        "piece_id": piece_id,
        "title": piece.get("title"),
        "status": piece.get("status"),
        "verdict": verdict,
        "keywords": keywords,
        "seo": seo,
        "ai": ai,
        "traffic": traffic,
    }


# ── POST /pieces/{id}/refine ─────────────────────────────────────────────────


REFINE_INTENTS = {
    "grammar": (
        "Förbättra grammatik och formulering utan att ändra innehållet eller längden nämnvärt. "
        "Behåll svenska där det är svenska, behåll engelska där det är engelska."
    ),
    "tone": (
        "Anpassa tonen så den blir mer naturlig och varumärkesvänlig. Undvik översäljande språk. "
        "Behåll faktainnehållet."
    ),
    "seo": (
        "Förbättra SEO-täckningen: gör rubriker och underrubriker tydligare, lägg in det primära "
        "sökordet naturligt i intro, mellanrubriker och slutet. Lägg INTE till nyckelord på ett "
        "sätt som ser stoppat ut."
    ),
    "shorten": "Förkorta texten till ungefär 70% av sin längd utan att tappa kärnpoängerna.",
    "expand": "Utveckla resonemanget med konkret exempel och en skarp avslutning. Behåll tonen.",
}


class RefineRequest(BaseModel):
    text: str
    intent: str = "grammar"  # one of REFINE_INTENTS
    target_keyword: Optional[str] = None
    audience: Optional[str] = None  # supplied by the dashboard from user_settings


@router.post("/pieces/{piece_id}/refine")
async def piece_refine(piece_id: str, payload: RefineRequest, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    intent = (payload.intent or "grammar").lower()
    instruction = REFINE_INTENTS.get(intent)
    if not instruction:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown intent. Use one of: {', '.join(REFINE_INTENTS)}",
        )

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    if len(text) > 12000:
        raise HTTPException(status_code=400, detail="text too long (max 12k chars)")

    config = await get_tenant_config(tenant_id)
    api_key = (
        getattr(config, "anthropic_api_key", None)
        if config else None
    ) or settings.ANTHROPIC_API_KEY
    if not api_key:
        raise HTTPException(status_code=503, detail="No Anthropic API key configured")

    target_kw = (payload.target_keyword or "").strip()
    audience = (payload.audience or "").strip()
    extras: List[str] = []
    if target_kw:
        extras.append(f"Primärt sökord: {target_kw}.")
    if audience:
        extras.append(f"Målgrupp: {audience}.")
    extras_str = " ".join(extras)

    system = (
        "Du är en svensk content-redaktör. Du justerar text enligt en specifik instruktion "
        "utan att lägga till påhittad fakta. Svara ALLTID med endast den förbättrade texten — "
        "inga kommentarer, ingen markdown-fence, ingen rubrik som 'Förbättrad version'."
    )
    user = (
        f"Instruktion: {instruction}\n"
        f"{extras_str}\n\n"
        f"Originaltext:\n---\n{text}\n---\n\n"
        f"Returnera endast den förbättrade texten."
    )

    client = Anthropic(api_key=api_key)
    try:
        import asyncio

        def _call():
            return client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
            )

        response = await asyncio.to_thread(_call)
        out = response.content[0].text.strip() if response.content else ""
        # Strip stray code fences if Claude ignored the instruction
        out = re.sub(r"^```[a-zA-Z0-9]*\n?", "", out)
        out = re.sub(r"\n?```$", "", out)
        return {"piece_id": piece_id, "intent": intent, "refined_text": out.strip()}
    except Exception as e:
        logger.error(f"piece_refine failed: {e}")
        raise HTTPException(status_code=502, detail=f"Refine failed: {e}")
