"""
Social suggestion endpoint.
Generates social post ideas the user can import into the social agent.
"""

import json
import logging
from typing import List

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class SocialSuggestRequest(BaseModel):
    brand_name: str = ""
    domain: str = ""
    brand_description: str = ""
    target_audience: str = ""
    competitors: List[str] = []


def _load_brand_context(tenant_id: str) -> dict:
    try:
        sb = get_supabase()
        data = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
        return data.data.get("settings", {}) if data.data else {}
    except Exception:
        return {}


@router.post("/suggest-posts")
async def suggest_social_posts(payload: SocialSuggestRequest, request: Request):
    """Suggest 6 social post drafts split across X, LinkedIn, and Reddit."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    if not payload.brand_name and tenant_id != "default":
        s = _load_brand_context(tenant_id)
        payload.brand_name = payload.brand_name or s.get("brand_name", "")
        payload.domain = payload.domain or s.get("domain", "")
        payload.brand_description = payload.brand_description or s.get("brand_description", "")
        payload.target_audience = payload.target_audience or s.get("target_audience", "")
        payload.competitors = payload.competitors or s.get("competitors", [])

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = f"""You are a B2B social media manager. Propose 6 ready-to-post drafts for this brand: 2 for X (twitter), 2 for LinkedIn, 2 for Reddit.

Brand: {payload.brand_name}
Website: {payload.domain}
Description: {payload.brand_description}
Target audience: {payload.target_audience}
Competitors: {', '.join(payload.competitors) if payload.competitors else 'N/A'}

Constraints per platform:
- twitter: <=270 characters, conversational
- linkedin: 100-200 words, professional, ends with a question or CTA
- reddit: 80-200 words, value-first, no overt sales pitch, suggest a relevant subreddit in the reason

Return ONLY a JSON array (no markdown, no code fences):
[
  {{
    "platform": "twitter|linkedin|reddit",
    "content": "ready-to-post text",
    "reason": "one sentence why this works"
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
            suggestions = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                suggestions = json.loads(text.strip())
            else:
                suggestions = []

        return {"suggestions": suggestions if isinstance(suggestions, list) else []}
    except Exception as e:
        logger.error(f"suggest_social_posts error: {e}")
        return {"suggestions": [], "error": str(e)}
