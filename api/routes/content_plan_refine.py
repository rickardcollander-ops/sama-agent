"""
AI refine endpoint for content plan items.

Mirrors the existing ``/pieces/{id}/refine`` flow, but tailored for the
short text fields on a plan item (title, topic). The dashboard uses this
from the chip-editing modal so users can iterate on a calendar idea
without first promoting it to a full content piece.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional

from anthropic import Anthropic
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


PLAN_REFINE_INTENTS = {
    "grammar": (
        "Förbättra grammatik och formulering utan att ändra innehållet eller längden nämnvärt. "
        "Behåll svenska där det är svenska, behåll engelska där det är engelska."
    ),
    "tone": (
        "Anpassa tonen så den blir mer naturlig och varumärkesvänlig. Undvik översäljande språk. "
        "Behåll faktainnehållet."
    ),
    "seo": (
        "Förbättra SEO: gör formuleringen tydligare och mer sökmotorvänlig, väv in det primära "
        "sökordet naturligt. Lägg INTE till sökord på ett sätt som ser stoppat ut."
    ),
    "shorten": "Förkorta texten till ungefär 70% av sin längd utan att tappa kärnpoängen.",
    "expand": "Utveckla resonemanget kort. Behåll tonen.",
    "punchier": "Gör texten mer slagkraftig och konkret. Undvik fluff. Behåll faktainnehållet.",
}


class PlanRefineRequest(BaseModel):
    text: str
    intent: str = "grammar"
    field: str = "title"  # "title" | "topic" — informational, used in prompt
    target_keyword: Optional[str] = None
    audience: Optional[str] = None


@router.post("/plan/{item_id}/refine")
async def plan_refine(item_id: str, payload: PlanRefineRequest, request: Request):
    """AI-rewrite a plan item's title or topic.

    Returns the refined text; the dashboard applies it via
    ``PATCH /plan/{id}`` once the user accepts the suggestion.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    intent = (payload.intent or "grammar").lower()
    instruction = PLAN_REFINE_INTENTS.get(intent)
    if not instruction:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown intent. Use one of: {', '.join(PLAN_REFINE_INTENTS)}",
        )

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="text too long (max 4k chars)")

    config = await get_tenant_config(tenant_id)
    api_key = (
        getattr(config, "anthropic_api_key", None) if config else None
    ) or settings.ANTHROPIC_API_KEY
    if not api_key:
        raise HTTPException(status_code=503, detail="No Anthropic API key configured")

    field = (payload.field or "title").strip().lower()
    target_kw = (payload.target_keyword or "").strip()
    audience = (payload.audience or "").strip()
    extras: List[str] = []
    if target_kw:
        extras.append(f"Primärt sökord: {target_kw}.")
    if audience:
        extras.append(f"Målgrupp: {audience}.")
    extras_str = " ".join(extras)

    field_hint = (
        "Det här är en artikeltitel — håll den kort och catchig (max ~80 tecken)."
        if field == "title"
        else "Det här är en kort beskrivning av vad artikeln ska handla om — en till två meningar."
    )

    system = (
        "Du är en svensk content-redaktör. Du justerar text enligt en specifik instruktion "
        "utan att lägga till påhittad fakta. Svara ALLTID med endast den förbättrade texten — "
        "inga kommentarer, ingen markdown-fence, inga citationstecken runt resultatet."
    )
    user = (
        f"Instruktion: {instruction}\n"
        f"{field_hint}\n"
        f"{extras_str}\n\n"
        f"Originaltext:\n---\n{text}\n---\n\n"
        f"Returnera endast den förbättrade texten."
    )

    client = Anthropic(api_key=api_key)
    try:
        def _call():
            return client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )

        response = await asyncio.to_thread(_call)
        out = response.content[0].text.strip() if response.content else ""
        out = re.sub(r"^```[a-zA-Z0-9]*\n?", "", out)
        out = re.sub(r"\n?```$", "", out)
        out = out.strip().strip('"').strip("'").strip()
        return {
            "item_id": item_id,
            "field": field,
            "intent": intent,
            "refined_text": out,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"plan_refine failed: {e}")
        raise HTTPException(status_code=502, detail=f"Refine failed: {e}")
