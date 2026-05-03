"""
Content suggestion endpoint.
Generates a list of topic ideas the user can import into the content agent.
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


class ContentSuggestRequest(BaseModel):
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


@router.post("/suggest-topics")
async def suggest_content_topics(payload: ContentSuggestRequest, request: Request):
    """Suggest 5-8 content topics the user can hand off to the content agent."""
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
        prompt = f"""You are a B2B SaaS content strategist. Suggest 6 content ideas for the following brand.

Brand: {payload.brand_name}
Website: {payload.domain}
Description: {payload.brand_description}
Target audience: {payload.target_audience}
Competitors: {', '.join(payload.competitors) if payload.competitors else 'N/A'}

Mix the formats: 2 blog_article, 2 linkedin_post, 2 email.
For each idea, give a clear topic, the format type, and a one-sentence reason.

Return ONLY a JSON array (no markdown, no code fences):
[
  {{"topic": "...", "type": "blog_article|linkedin_post|email", "reason": "..."}}
]
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        try:
            topics = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                topics = json.loads(text.strip())
            else:
                topics = []

        return {"topics": topics if isinstance(topics, list) else []}
    except Exception as e:
        logger.error(f"suggest_content_topics error: {e}")
        return {"topics": [], "error": str(e)}
