"""
Google OAuth2 helper for Google APIs (Search Console, Ads)
Uses refresh token flow - no browser interaction needed at runtime.
"""

import logging
import httpx
from typing import Optional, Dict, Any

from shared.config import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"

# Cache access tokens in memory
_token_cache: Dict[str, Dict[str, Any]] = {}


async def get_access_token(scope: str = "gsc") -> Optional[str]:
    """
    Get a valid Google OAuth2 access token using refresh token.
    
    Args:
        scope: Which token to get - 'gsc' for Search Console, 'ads' for Google Ads
    
    Returns:
        Access token string, or None if not configured
    """
    import time
    
    # Check cache
    cached = _token_cache.get(scope)
    if cached and cached.get("expires_at", 0) > time.time() + 60:
        return cached["access_token"]
    
    # Determine which credentials to use
    if scope == "ads":
        client_id = settings.GOOGLE_ADS_CLIENT_ID or settings.GOOGLE_CLIENT_ID
        client_secret = settings.GOOGLE_ADS_CLIENT_SECRET or settings.GOOGLE_CLIENT_SECRET
        refresh_token = settings.GOOGLE_ADS_REFRESH_TOKEN
    else:
        client_id = settings.GOOGLE_CLIENT_ID
        client_secret = settings.GOOGLE_CLIENT_SECRET
        refresh_token = getattr(settings, 'GOOGLE_REFRESH_TOKEN', '')
    
    if not client_id or not client_secret or not refresh_token:
        return None
    
    # Exchange refresh token for access token
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        })
        
        if resp.status_code != 200:
            logger.error(f"Google OAuth token refresh failed: {resp.status_code} {resp.text}")
            return None
        
        data = resp.json()
        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        
        # Cache it
        _token_cache[scope] = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in
        }
        
        logger.info(f"✅ Google OAuth token refreshed for {scope}")
        return access_token


def is_gsc_configured() -> bool:
    """Check if Google Search Console credentials are configured"""
    return bool(
        settings.GOOGLE_CLIENT_ID
        and settings.GOOGLE_CLIENT_SECRET
        and getattr(settings, 'GOOGLE_REFRESH_TOKEN', '')
    )


def is_ads_configured() -> bool:
    """Check if Google Ads credentials are configured"""
    return bool(
        settings.GOOGLE_ADS_DEVELOPER_TOKEN
        and settings.GOOGLE_ADS_CUSTOMER_ID
        and (settings.GOOGLE_ADS_CLIENT_ID or settings.GOOGLE_CLIENT_ID)
        and settings.GOOGLE_ADS_REFRESH_TOKEN
    )
