"""
Ads Creatives API Routes
AI-powered ad copy generation, screenshot analysis, and CRUD for ad creative drafts.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request / Response models ────────────────────────────────────────────────

class GenerateCopyRequest(BaseModel):
    platform: str = "google"
    format: str = "responsive_search"
    goal: str = "conversions"
    brand_description: str = ""
    target_audience: str = ""
    competitors: Optional[str] = ""


class AnalyzeScreenshotRequest(BaseModel):
    image_base64: str
    platform: str = "meta"
    brand_context: Optional[str] = ""


class AdCreativeCreate(BaseModel):
    platform: str
    format: Optional[str] = None
    headline: Optional[str] = None
    body_text: Optional[str] = None
    cta: Optional[str] = None
    image_url: Optional[str] = None
    campaign_id: Optional[str] = None


class AdCreativeUpdate(BaseModel):
    headline: Optional[str] = None
    body_text: Optional[str] = None
    cta: Optional[str] = None
    image_url: Optional[str] = None
    format: Optional[str] = None
    platform: Optional[str] = None


def _ensure_numeric_perf(row: dict) -> dict:
    """Ensure performance sub-object has zeros instead of nulls."""
    perf = row.get("performance") or {}
    for key in ("impressions", "clicks", "conversions", "cost"):
        if perf.get(key) is None:
            perf[key] = 0
    for key in ("ctr", "cpa"):
        if perf.get(key) is None:
            perf[key] = 0.0
    row["performance"] = perf
    return row


# ── AI: Generate ad copy ────────────────────────────────────────────────────

@router.post("/generate-copy")
async def generate_ad_copy(payload: GenerateCopyRequest):
    """Use Anthropic Claude to generate ad copy."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        prompt = f"""You are an expert digital advertising copywriter.
Generate ad copy for the following brief:

Platform: {payload.platform}
Ad format: {payload.format}
Campaign goal: {payload.goal}
Brand: {payload.brand_description}
Target audience: {payload.target_audience}
Competitors to differentiate from: {payload.competitors}

Return ONLY a JSON object with these keys (no markdown, no code fences):
{{
  "headline": "...",
  "body": "...",
  "cta": "...",
  "rationale": "..."
}}
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = message.content[0].text.strip()
        # Try to parse JSON from the response
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Attempt to extract JSON from markdown code block
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                result = json.loads(text.strip())
            else:
                result = {"headline": text[:100], "body": text, "cta": "Learn More", "rationale": "Raw AI output"}

        return {
            "headline": result.get("headline", ""),
            "body": result.get("body", ""),
            "cta": result.get("cta", ""),
            "rationale": result.get("rationale", ""),
        }
    except Exception as e:
        logger.error(f"generate_ad_copy error: {e}")
        return {
            "headline": "",
            "body": "",
            "cta": "",
            "rationale": f"Error generating copy: {str(e)}",
        }


# ── AI: Analyze screenshot ──────────────────────────────────────────────────

@router.post("/analyze-screenshot")
async def analyze_ad_screenshot(payload: AnalyzeScreenshotRequest):
    """Use Anthropic Claude Vision to analyze an uploaded ad screenshot."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        prompt = f"""You are an expert digital advertising analyst.
Analyze this ad screenshot from {payload.platform}.
Brand context: {payload.brand_context}

Provide your analysis as a JSON object with these keys (no markdown, no code fences):
{{
  "metrics": {{"estimated_ctr": 0.0, "quality_score": 0, "relevance_score": 0}},
  "performance_assessment": "...",
  "recommendations": ["...", "..."],
  "industry_benchmarks": {{"avg_ctr": 0.0, "avg_quality_score": 0}}
}}
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": payload.image_base64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        import json
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
                    "metrics": {"estimated_ctr": 0, "quality_score": 0, "relevance_score": 0},
                    "performance_assessment": text,
                    "recommendations": [],
                    "industry_benchmarks": {"avg_ctr": 0, "avg_quality_score": 0},
                }

        return {
            "metrics": result.get("metrics", {}),
            "performance_assessment": result.get("performance_assessment", ""),
            "recommendations": result.get("recommendations", []),
            "industry_benchmarks": result.get("industry_benchmarks", {}),
        }
    except Exception as e:
        logger.error(f"analyze_ad_screenshot error: {e}")
        return {
            "metrics": {"estimated_ctr": 0, "quality_score": 0, "relevance_score": 0},
            "performance_assessment": f"Error: {str(e)}",
            "recommendations": [],
            "industry_benchmarks": {"avg_ctr": 0, "avg_quality_score": 0},
        }


# ── CRUD: List creatives ────────────────────────────────────────────────────

@router.get("/creatives")
async def list_ad_creatives(request: Request, limit: int = 50):
    """List saved ad creative drafts for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    if settings.DEMO_MODE:
        from shared.demo_data import DEMO_AD_CREATIVES
        return {"creatives": DEMO_AD_CREATIVES}

    try:
        sb = get_supabase()
        result = (
            sb.table("ad_creatives")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        creatives = [_ensure_numeric_perf(r) for r in (result.data or [])]
        return {"creatives": creatives}
    except Exception as e:
        logger.error(f"list_ad_creatives error: {e}")
        return {"creatives": []}


# ── CRUD: Create creative ───────────────────────────────────────────────────

@router.post("/creatives")
async def create_ad_creative(request: Request, payload: AdCreativeCreate):
    """Save a new ad creative draft."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        data = {
            **payload.model_dump(),
            "tenant_id": tenant_id,
            "is_manual": True,
            "performance": {"impressions": 0, "clicks": 0, "ctr": 0, "conversions": 0, "cost": 0, "cpa": 0},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = sb.table("ad_creatives").insert(data).execute()
        return {"success": True, "creative": result.data[0] if result.data else data}
    except Exception as e:
        logger.error(f"create_ad_creative error: {e}")
        return {"success": False, "error": str(e)}


# ── CRUD: Update creative ───────────────────────────────────────────────────

@router.patch("/creatives/{creative_id}")
async def update_ad_creative(creative_id: str, payload: AdCreativeUpdate):
    """Update an existing ad creative draft."""
    try:
        sb = get_supabase()
        update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not update_data:
            return {"success": True, "message": "Nothing to update"}
        result = sb.table("ad_creatives").update(update_data).eq("id", creative_id).execute()
        if result.data:
            return {"success": True, "creative": _ensure_numeric_perf(result.data[0])}
        return {"success": True, "message": "Updated"}
    except Exception as e:
        logger.error(f"update_ad_creative error: {e}")
        return {"success": False, "error": str(e)}
