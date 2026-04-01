"""
Content Generation API Route
Uses Anthropic Claude to generate various types of marketing content.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from shared.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


class ContentGenerateRequest(BaseModel):
    type: str = "blog_article"  # linkedin_post | blog_article | email
    topic: Optional[str] = None
    brand_description: str = ""
    target_audience: str = ""
    tone: str = "professional"


@router.post("/generate")
async def generate_content(payload: ContentGenerateRequest):
    """
    Generate marketing content using Anthropic Claude.
    Returns title, body, platform, and suggestions.
    """
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        content_type_label = payload.type.replace("_", " ").title()

        prompt = f"""You are an expert B2B SaaS content marketer.
Generate a {content_type_label} based on the following brief:

Topic: {payload.topic or 'Choose a relevant topic'}
Brand: {payload.brand_description}
Target audience: {payload.target_audience}
Tone: {payload.tone}

Return ONLY a JSON object (no markdown, no code fences) with these keys:
{{
  "title": "...",
  "body": "...",
  "platform": "{payload.type}",
  "suggestions": ["improvement suggestion 1", "improvement suggestion 2", "improvement suggestion 3"]
}}

For blog_article: body should be 800-1200 words in markdown.
For linkedin_post: body should be 100-200 words, optimized for LinkedIn.
For email: body should include subject line (in title), and the email body with a clear CTA.
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                result = json.loads(text.strip())
            else:
                result = {
                    "title": "Generated Content",
                    "body": text,
                    "platform": payload.type,
                    "suggestions": [],
                }

        return {
            "title": result.get("title", ""),
            "body": result.get("body", ""),
            "platform": result.get("platform", payload.type),
            "suggestions": result.get("suggestions", []),
        }
    except Exception as e:
        logger.error(f"generate_content error: {e}")
        return {
            "title": "",
            "body": "",
            "platform": payload.type,
            "suggestions": [f"Error generating content: {str(e)}"],
        }
