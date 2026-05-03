"""
Ad suggestion endpoint.
Generates ad creative suggestions the user can import into the ads agent.
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


class AdsSuggestRequest(BaseModel):
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


@router.post("/suggest-campaigns")
async def suggest_ad_campaigns(payload: AdsSuggestRequest, request: Request):
    """Suggest 4-6 ad creative ideas spread across platforms."""
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
        prompt = f"""You are a paid-media strategist. Propose 5 ad creative ideas for this brand, mixed across Meta, LinkedIn, and Google.

Brand: {payload.brand_name}
Website: {payload.domain}
Description: {payload.brand_description}
Target audience: {payload.target_audience}
Competitors: {', '.join(payload.competitors) if payload.competitors else 'N/A'}

Constraints per platform:
- meta: headline <=40 chars, body <=125 chars
- linkedin: headline <=70 chars, body <=600 chars
- google: headline <=30 chars, body <=90 chars

Return ONLY a JSON array (no markdown, no code fences):
[
  {{
    "platform": "meta|linkedin|google",
    "goal": "awareness|leads|traffic|conversions",
    "headline": "...",
    "body": "...",
    "cta": "Learn More|Contact Us|Book Demo|Download",
    "reason": "one sentence why this should work"
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
        logger.error(f"suggest_ad_campaigns error: {e}")
        return {"suggestions": [], "error": str(e)}
