"""
Generate per-platform social posts tied to a parent article.

Used by content_plan_creator: for each chosen platform, write a post that
references the article and contains an {{ARTICLE_URL}} placeholder. The
placeholder is filled at email-send time once the article actually goes
live (so the link is the real published URL, not a stub).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from anthropic import Anthropic

from shared.config import settings
from .brand_voice import BrandVoice, TenantBrandVoice

logger = logging.getLogger(__name__)

MODEL = getattr(settings, "CLAUDE_MODEL", "claude-sonnet-4-6")


_PLATFORM_INSTRUCTIONS = {
    "linkedin": (
        "Write a single LinkedIn post (600-1400 chars). Hook on line 1 (visible "
        "before 'see more'). 3-6 short paragraphs separated by blank lines. "
        "End with the article link {{ARTICLE_URL}} on its own line. 0-2 hashtags max."
    ),
    "x": (
        "Write either a single tweet (<=280 chars) or a thread of 3-5 tweets each "
        "<=280 chars. If a thread, separate tweets with two blank lines. "
        "Place {{ARTICLE_URL}} on the LAST tweet only. 0-1 emoji, 0-2 hashtags max."
    ),
    "instagram": (
        "Write an Instagram caption (100-200 words). Hook on line 1. Line breaks "
        "between thoughts. Include 'Link in bio: {{ARTICLE_URL}}' near the bottom. "
        "End with 5-8 relevant hashtags grouped on a single line."
    ),
    "facebook": (
        "Write a Facebook post (100-250 words). Conversational opening (often a "
        "question or scenario). One insight + one question to drive comments. "
        "End with the article link {{ARTICLE_URL}} on its own line. 0-1 emoji."
    ),
}


async def generate_for_article(
    *,
    tenant_id: str,
    voice: TenantBrandVoice,
    brand_name: str,
    article_title: str,
    article_summary: str,
    platform: str,
    link_placeholder: str = "{{ARTICLE_URL}}",
) -> Dict[str, Any]:
    """Generate a platform-specific social post for `article`. Returns {content, platform}."""
    platform = (platform or "").lower().strip()
    if platform not in _PLATFORM_INSTRUCTIONS:
        raise ValueError(f"unsupported platform: {platform}")
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    system_prompt = voice.get_system_prompt(f"social_{platform}", brand_name=brand_name)
    instruction = _PLATFORM_INSTRUCTIONS[platform]

    user_prompt = f"""You're going to promote a new article on {platform}.

Article title: {article_title}
Article summary (excerpt for context, do NOT quote verbatim):
{article_summary}

Instructions: {instruction}

Return ONLY the post text -- no JSON, no markdown headings, no commentary, no quotation marks around the whole post. Use {link_placeholder} as the placeholder for the article URL; the real URL will be substituted before sending.
Reminder: NEVER use em-dashes. Use a comma or period instead.
"""
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def _call():
        return client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

    response = await asyncio.to_thread(_call)
    content = response.content[0].text.strip()

    # Strip leading/trailing quotes if Claude added them
    if content.startswith('"') and content.endswith('"') and len(content) > 2:
        content = content[1:-1]

    # Em-dash cleanup
    content = BrandVoice.cleanup_ai_tells(content)

    # Ensure the placeholder is present (Claude sometimes drops it for X threads)
    if link_placeholder not in content:
        content = f"{content.rstrip()}\n\n{link_placeholder}"

    return {
        "platform": platform,
        "content": content,
        "link_placeholder": link_placeholder,
    }
