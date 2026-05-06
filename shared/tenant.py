"""
Tenant Configuration for Multi-Tenancy Support
Loads per-tenant settings from the user_settings table in Supabase,
with fallback to global environment-variable settings.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)

# In-memory cache keyed by (account_id, site_id) so a single workspace owning
# multiple sites doesn't collide on a single cached config. The historical
# single-key path remains available — when only tenant_id is supplied we
# treat it as both the account and the site dimension.
_tenant_cache: Dict[Tuple[str, str], tuple] = {}
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
    #
    # Tenant-identifying fields are read ONLY from the per-tenant settings
    # blob. Falling back to global env vars (BRAND_NAME, SUCCESSIFIER_*)
    # caused cross-tenant bleed: a tenant with no explicit brand_name in
    # their settings would inherit "Successifier" / successifier.com from
    # the legacy single-tenant deploy and the LLM would generate
    # Successifier-flavoured suggestions for them.

    @staticmethod
    def _str_or_empty(val: Any) -> str:
        return val.strip() if isinstance(val, str) and val.strip() else ""

    @property
    def brand_name(self) -> str:
        return self._str_or_empty(self._settings.get("brand_name"))

    @property
    def domain(self) -> str:
        return self._str_or_empty(self._settings.get("domain"))

    @property
    def site_url(self) -> str:
        explicit = self._str_or_empty(self._settings.get("site_url"))
        if explicit:
            return explicit
        domain = self.domain
        return f"https://{domain}" if domain else ""

    @property
    def cms_api_url(self) -> str:
        explicit = self._str_or_empty(self._settings.get("cms_api_url"))
        if explicit:
            return explicit
        domain = self.domain
        return f"https://{domain}/api" if domain else ""

    @property
    def cms_api_key(self) -> str:
        return self._str_or_empty(self._settings.get("cms_api_key"))

    # ── SEO ───────────────────────────────────────────────────────────

    @property
    def gsc_site_url(self) -> str:
        explicit = self._str_or_empty(self._settings.get("gsc_site_url"))
        if explicit:
            return explicit
        domain = self.domain
        return f"sc-domain:{domain}" if domain else ""

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

async def get_tenant_config(
    tenant_id: str,
    *,
    account_id: Optional[str] = None,
    site_id: Optional[str] = None,
) -> TenantConfig:
    """
    Load (or return cached) TenantConfig for the given tenant identity.

    Backwards compatible: callers that only have ``tenant_id`` keep working —
    the function looks up the per-site settings exactly as before. Newer
    callers can pass ``account_id`` and ``site_id`` so the cache partitions
    correctly when a single account owns multiple sites.

    Lookup order for the underlying settings row:
      1. ``user_sites.id`` matching site_id / account_id / tenant_id
         (current source of truth — the dashboard writes per-site brand
         context to user_sites.settings keyed by site id)
      2. ``user_settings.user_id`` matching site_id / account_id / tenant_id
         (legacy single-tenant rows; preserved for old installs that
         haven't migrated to user_sites yet)

    Results are cached for 5 minutes to minimise DB round-trips. The cache
    key is the (account, site) pair so two sites under one account don't
    share a single cached blob.
    """
    now = time.time()

    cache_account = account_id or tenant_id
    cache_site = site_id or tenant_id
    cache_key = (cache_account, cache_site)

    # Check cache
    if cache_key in _tenant_cache:
        cached_config, expiry = _tenant_cache[cache_key]
        if now < expiry:
            return cached_config

    # Load from Supabase. Try the most specific lookup first and fall back
    # to broader keys so legacy installs (where only tenant_id == user_id is
    # populated) keep working.
    tenant_settings: Dict[str, Any] = {}
    sb = get_supabase()
    lookup_keys: List[str] = []
    for key in (site_id, account_id, tenant_id):
        if key and key not in lookup_keys:
            lookup_keys.append(key)

    # 1. user_sites.id (current dashboard storage). The dashboard's
    # X-Tenant-ID is the user_sites.id, so this hits on any tenant that has
    # gone through the multi-site migration — which is everyone created
    # after the migration shipped, plus all view-as targets.
    matched_via = None
    for key in lookup_keys:
        try:
            result = (
                sb.table("user_sites")
                .select("settings")
                .eq("id", key)
                .single()
                .execute()
            )
        except Exception as e:
            logger.debug(f"user_sites lookup failed for {key}: {e}")
            continue
        if result.data and isinstance(result.data.get("settings"), dict):
            tenant_settings = result.data["settings"] or {}
            matched_via = ("user_sites.id", key)
            break

    # 2. user_settings.user_id (legacy single-tenant table). Only consulted
    # when the per-site lookup found nothing.
    if not tenant_settings:
        for key in lookup_keys:
            try:
                result = (
                    sb.table("user_settings")
                    .select("settings")
                    .eq("user_id", key)
                    .single()
                    .execute()
                )
            except Exception as e:
                logger.debug(f"user_settings lookup failed for {key}: {e}")
                continue
            if result.data:
                tenant_settings = result.data.get("settings", {}) or {}
                matched_via = ("user_settings.user_id", key)
                break

    if matched_via:
        logger.debug(
            "Loaded tenant config from %s=%s for account=%s site=%s",
            matched_via[0], matched_via[1], cache_account, cache_site,
        )
    else:
        logger.debug(
            "No user_sites or user_settings row for account=%s site=%s — using defaults",
            cache_account, cache_site,
        )

    config = TenantConfig(cache_site, tenant_settings)

    # Cache it
    _tenant_cache[cache_key] = (config, now + _CACHE_TTL_SECONDS)

    return config


def invalidate_tenant_cache(
    tenant_id: Optional[str] = None,
    *,
    account_id: Optional[str] = None,
    site_id: Optional[str] = None,
) -> None:
    """Remove cached config for a tenant, or all tenants if everything is None.

    Pass ``site_id`` to drop a single site's entry; pass ``account_id`` to drop
    every site cached under that account. With nothing supplied (or just the
    legacy ``tenant_id``) the historical "single key" semantics are preserved.
    """
    if tenant_id is None and account_id is None and site_id is None:
        _tenant_cache.clear()
        return

    if account_id and not site_id:
        # Drop every cached site under this account.
        for key in [k for k in _tenant_cache if k[0] == account_id]:
            _tenant_cache.pop(key, None)
        return

    cache_account = account_id or tenant_id
    cache_site = site_id or tenant_id
    _tenant_cache.pop((cache_account, cache_site), None)
