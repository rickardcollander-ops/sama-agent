"""
User Settings API Routes
Stores per-user configuration: API keys, brand info, competitors, GEO queries.

Secrets (``*_api_key``, ``*_token``, ``client_secret``, ...) are stored in the
encrypted column ``settings_encrypted`` when ``MASTER_KMS_KEY`` is configured.
The plaintext column ``settings`` keeps the non-secret fields plus, during the
migration window, mirrored secrets so old readers keep working. Set
``READ_ENCRYPTED_ONLY=1`` once backfill completes to drop the plaintext path.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from shared.database import get_supabase
from shared.secrets_vault import (
    decrypt_payload,
    encrypt_payload,
    encryption_available,
    merge_with_secrets,
    split_secrets,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


class SettingsPayload(BaseModel):
    user_id: str
    settings: Dict[str, Any]


class SettingsResponse(BaseModel):
    user_id: str
    settings: Dict[str, Any]
    updated_at: Optional[str] = None


def _row_to_settings(row: Dict[str, Any]) -> Dict[str, Any]:
    """Materialise effective settings dict from a user_settings row,
    preferring decrypted secrets over plaintext when available."""
    plaintext = row.get("settings") or {}
    encrypted_blob = row.get("settings_encrypted")
    if encryption_available() and encrypted_blob:
        decrypted = decrypt_payload(encrypted_blob) or {}
        if _truthy("READ_ENCRYPTED_ONLY"):
            non_secrets, _ = split_secrets(plaintext)
            return merge_with_secrets(non_secrets, decrypted)
        return merge_with_secrets(plaintext, decrypted)
    return plaintext


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
                "settings": _row_to_settings(data.data),
                "updated_at": data.data.get("updated_at"),
            }
        return {"user_id": user_id, "settings": {}, "updated_at": None}
    except Exception as e:
        logger.error(f"get_user_settings error: {e}")
        return {"user_id": user_id, "settings": {}, "updated_at": None}


# ── Save settings ────────────────────────────────────────────────────────────

@router.post("/settings")
async def save_user_settings(payload: SettingsPayload):
    """Create or update user settings.

    Dual-write: secrets are encrypted into ``settings_encrypted``; the
    plaintext column keeps non-secret fields plus a redacted shadow of secret
    keys (so legacy code paths can still tell whether a secret is configured
    without exposing the value).
    """
    try:
        sb = get_supabase()
        non_secrets, secrets = split_secrets(payload.settings)
        now = datetime.now(timezone.utc).isoformat()
        row: Dict[str, Any] = {
            "user_id": payload.user_id,
            "updated_at": now,
        }

        if encryption_available() and secrets:
            blob = encrypt_payload(secrets)
            if blob:
                row["settings_encrypted"] = blob
                row["settings_encrypted_at"] = now
            # Plaintext column keeps non-secrets only; this stops the
            # cleartext API keys from sitting in the DB after a successful
            # encrypted write.
            row["settings"] = non_secrets
        else:
            # Encryption disabled — preserve legacy behaviour exactly.
            row["settings"] = payload.settings

        sb.table("user_settings").upsert(row, on_conflict="user_id").execute()
        return {
            "success": True,
            "user_id": payload.user_id,
            "encrypted": "settings_encrypted" in row,
        }
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
            .select("*")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        s = _row_to_settings(data.data) if data.data else {}
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
