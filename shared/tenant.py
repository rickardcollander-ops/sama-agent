"""
Tenant Configuration for Multi-Tenancy Support
Loads per-tenant settings from the user_settings table in Supabase,
with fallback to global environment-variable settings.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)

# In-memory cache: tenant_id -> (TenantConfig, expiry_timestamp)
_tenant_cache: Dict[str, tuple] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


class TenantConfig:
    """
    Per-tenant configuration that mirrors the global Settings but can be
    overridden via the user_settings table in Supabase.

    Any attribute not found in the tenant-specific settings falls back to
    the corresponding value on the global ``settings`` singleton.
    """

    def __init__(self, tenant_id: str, tenant_settings: Dict[str, Any]):
        self.tenant_id = tenant_id
        self._settings = tenant_settings  # raw dict from user_settings.settings JSONB

    # ── Convenience helpers ───────────────────────────────────────────

    def _get(self, key: str, default: Any = None) -> Any:
        """Return tenant value if present, else global settings value, else default."""
        val = self._settings.get(key)
        if val is not None and val != "":
            return val
        # Try global settings (env vars)
        global_val = getattr(settings, key, None)
        if global_val is not None:
            return global_val
        return default

    # ── Brand / Domain ────────────────────────────────────────────────

    @property
    def brand_name(self) -> str:
        return self._get("brand_name", self._get("BRAND_NAME", "Successifier"))

    @property
    def domain(self) -> str:
        return self._get("domain", self._get("SUCCESSIFIER_DOMAIN", "successifier.com"))

    @property
    def site_url(self) -> str:
        return self._get("site_url", self._get("SUCCESSIFIER_SITE_URL", f"https://{self.domain}"))

    @property
    def cms_api_url(self) -> str:
        return self._get("cms_api_url", self._get("SUCCESSIFIER_CMS_API_URL", f"https://{self.domain}/api"))

    @property
    def cms_api_key(self) -> str:
        return self._get("cms_api_key", self._get("SUCCESSIFIER_CMS_API_KEY", ""))

    # ── SEO ───────────────────────────────────────────────────────────

    @property
    def gsc_site_url(self) -> str:
        return self._get("gsc_site_url", self._get("GSC_SITE_URL", f"sc-domain:{self.domain}"))

    @property
    def competitors(self) -> List[str]:
        val = self._settings.get("competitors")
        if val and isinstance(val, list):
            return val
        return list(settings.SEO_COMPETITORS)

    @property
    def seo_competitors(self) -> List[str]:
        """Alias for competitors."""
        return self.competitors

    @property
    def geo_queries(self) -> List[str]:
        """User-configured AI visibility prompts (stored via the dashboard)."""
        val = self._settings.get("geo_queries")
        if val and isinstance(val, list):
            return [q for q in val if isinstance(q, str) and q.strip()]
        return []

    # ── Analytics ─────────────────────────────────────────────────────

    @property
    def ga4_property_id(self) -> str:
        return self._get("ga4_property_id", self._get("GA4_PROPERTY_ID", ""))

    # ── API Keys ──────────────────────────────────────────────────────

    @property
    def anthropic_api_key(self) -> str:
        # Always use the system Anthropic key. Customers no longer supply their own.
        return getattr(settings, "ANTHROPIC_API_KEY", "") or ""

    @property
    def semrush_api_key(self) -> str:
        return self._get("semrush_api_key", self._get("SEMRUSH_API_KEY", ""))

    @property
    def google_client_id(self) -> str:
        return self._get("google_client_id", self._get("GOOGLE_CLIENT_ID", ""))

    @property
    def google_client_secret(self) -> str:
        return self._get("google_client_secret", self._get("GOOGLE_CLIENT_SECRET", ""))

    @property
    def google_refresh_token(self) -> str:
        return self._get("google_refresh_token", self._get("GOOGLE_REFRESH_TOKEN", ""))

    # Twitter / X
    @property
    def twitter_api_key(self) -> str:
        return self._get("twitter_api_key", self._get("TWITTER_API_KEY", ""))

    @property
    def twitter_api_secret(self) -> str:
        return self._get("twitter_api_secret", self._get("TWITTER_API_SECRET", ""))

    @property
    def twitter_access_token(self) -> str:
        return self._get("twitter_access_token", self._get("TWITTER_ACCESS_TOKEN", ""))

    @property
    def twitter_access_secret(self) -> str:
        return self._get("twitter_access_secret", self._get("TWITTER_ACCESS_SECRET", ""))

    @property
    def twitter_bearer_token(self) -> str:
        return self._get("twitter_bearer_token", self._get("TWITTER_BEARER_TOKEN", ""))

    # LinkedIn
    @property
    def linkedin_access_token(self) -> str:
        return self._get("linkedin_access_token", self._get("LINKEDIN_ACCESS_TOKEN", ""))

    @property
    def linkedin_org_id(self) -> str:
        return self._get("linkedin_org_id", self._get("LINKEDIN_ORG_ID", ""))

    # Reddit
    @property
    def reddit_client_id(self) -> str:
        return self._get("reddit_client_id", self._get("REDDIT_CLIENT_ID", ""))

    @property
    def reddit_client_secret(self) -> str:
        return self._get("reddit_client_secret", self._get("REDDIT_CLIENT_SECRET", ""))

    @property
    def reddit_username(self) -> str:
        return self._get("reddit_username", self._get("REDDIT_USERNAME", ""))

    @property
    def reddit_password(self) -> str:
        return self._get("reddit_password", self._get("REDDIT_PASSWORD", ""))

    # ── Brand Voice ───────────────────────────────────────────────────

    @property
    def brand_voice_tone(self) -> str:
        return self._get("brand_voice_tone", "")

    @property
    def messaging_pillars(self) -> List[Dict[str, Any]]:
        val = self._settings.get("messaging_pillars")
        if val and isinstance(val, list):
            return val
        return []

    @property
    def proof_points(self) -> Dict[str, str]:
        val = self._settings.get("proof_points")
        if val and isinstance(val, dict):
            return val
        return {}

    # ── Automation Preferences ────────────────────────────────────────

    @property
    def auto_publish_blog_posts(self) -> bool:
        return self._get("auto_publish_blog_posts", self._get("AUTO_PUBLISH_BLOG_POSTS", False))

    @property
    def auto_publish_social_posts(self) -> bool:
        return self._get("auto_publish_social_posts", self._get("AUTO_PUBLISH_SOCIAL_POSTS", True))

    @property
    def auto_respond_reviews_positive(self) -> bool:
        return self._get("auto_respond_reviews_positive", self._get("AUTO_RESPOND_REVIEWS_POSITIVE", True))

    @property
    def auto_respond_reviews_negative(self) -> bool:
        return self._get("auto_respond_reviews_negative", self._get("AUTO_RESPOND_REVIEWS_NEGATIVE", False))

    # ── Review Platform URLs ──────────────────────────────────────────

    @property
    def review_platforms(self) -> Dict[str, Dict[str, Any]]:
        """Return tenant-specific review platform config or empty dict (agent falls back to defaults)."""
        val = self._settings.get("review_platforms")
        if val and isinstance(val, dict):
            return val
        return {}

    # ── Raw access ────────────────────────────────────────────────────

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Direct access to underlying tenant settings dict."""
        return self._settings.get(key, default)

    def __repr__(self) -> str:
        return f"<TenantConfig tenant_id={self.tenant_id!r} domain={self.domain!r}>"


# ── Public API ──────────────────────────────────────────────────────────

async def get_tenant_config(tenant_id: str) -> TenantConfig:
    """
    Load (or return cached) TenantConfig for the given tenant_id.

    The tenant_id maps to user_id in the user_settings table.
    Results are cached for 5 minutes to minimise DB round-trips.
    """
    now = time.time()

    # Check cache
    if tenant_id in _tenant_cache:
        cached_config, expiry = _tenant_cache[tenant_id]
        if now < expiry:
            return cached_config

    # Load from Supabase
    tenant_settings: Dict[str, Any] = {}
    try:
        sb = get_supabase()
        result = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        if result.data:
            tenant_settings = result.data.get("settings", {}) or {}
            logger.debug(f"Loaded tenant config for {tenant_id} from DB")
        else:
            logger.debug(f"No tenant settings found for {tenant_id}, using defaults")
    except Exception as e:
        logger.warning(f"Failed to load tenant config for {tenant_id}: {e}. Using defaults.")

    config = TenantConfig(tenant_id, tenant_settings)

    # Cache it
    _tenant_cache[tenant_id] = (config, now + _CACHE_TTL_SECONDS)

    return config


def invalidate_tenant_cache(tenant_id: Optional[str] = None) -> None:
    """Remove cached config for a tenant, or all tenants if tenant_id is None."""
    if tenant_id is None:
        _tenant_cache.clear()
    else:
        _tenant_cache.pop(tenant_id, None)
