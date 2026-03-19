"""
User Settings API Routes
Stores per-user configuration: API keys, brand info, competitors, GEO queries.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class SettingsPayload(BaseModel):
    user_id: str
    settings: Dict[str, Any]


class SettingsResponse(BaseModel):
    user_id: str
    settings: Dict[str, Any]
    updated_at: Optional[str] = None


# ── Get settings ──────────────────────────────────────────────────────────────

@router.get("/settings/{user_id}")
async def get_user_settings(user_id: str):
    """Retrieve settings for a specific user"""
    try:
        sb = get_supabase()
        data = (
            sb.table("user_settings")
            .select("*")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if data.data:
            return {
                "user_id": data.data["user_id"],
                "settings": data.data.get("settings", {}),
                "updated_at": data.data.get("updated_at"),
            }
        return {"user_id": user_id, "settings": {}, "updated_at": None}
    except Exception as e:
        logger.error(f"get_user_settings error: {e}")
        return {"user_id": user_id, "settings": {}, "updated_at": None}


# ── Save settings ────────────────────────────────────────────────────────────

@router.post("/settings")
async def save_user_settings(payload: SettingsPayload):
    """Create or update user settings"""
    try:
        sb = get_supabase()
        sb.table("user_settings").upsert(
            {
                "user_id": payload.user_id,
                "settings": payload.settings,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="user_id",
        ).execute()
        return {"success": True, "user_id": payload.user_id}
    except Exception as e:
        logger.error(f"save_user_settings error: {e}")
        return {"success": False, "error": str(e)}


# ── Get GEO config for agent use ──────────────────────────────────────────────

@router.get("/settings/{user_id}/geo-config")
async def get_geo_config(user_id: str):
    """Return just the GEO-relevant config (queries, platforms, brand, competitors)
    for the AI Visibility agent to use."""
    try:
        sb = get_supabase()
        data = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        s = data.data.get("settings", {}) if data.data else {}
        return {
            "brand_name": s.get("brand_name", ""),
            "domain": s.get("domain", ""),
            "competitors": s.get("competitors", []),
            "geo_queries": s.get("geo_queries", []),
            "geo_platforms": s.get("geo_platforms", []),
            "openai_api_key": bool(s.get("openai_api_key")),
            "anthropic_api_key": bool(s.get("anthropic_api_key")),
            "perplexity_api_key": bool(s.get("perplexity_api_key")),
            "google_api_key": bool(s.get("google_api_key")),
        }
    except Exception as e:
        logger.error(f"get_geo_config error: {e}")
        return {
            "brand_name": "",
            "domain": "",
            "competitors": [],
            "geo_queries": [],
            "geo_platforms": [],
        }
