"""
Tenant-Aware Agent Factory
Instantiates agents with per-tenant configuration instead of global singletons.
"""

import logging
from typing import Optional

from shared.tenant import TenantConfig, get_tenant_config

logger = logging.getLogger(__name__)


async def get_seo_agent(tenant_id: str):
    """Create an SEOAgent configured for the given tenant."""
    from agents.seo import SEOAgent
    config = await get_tenant_config(tenant_id)
    return SEOAgent(tenant_config=config)


async def get_content_agent(tenant_id: str):
    """Create a ContentAgent configured for the given tenant."""
    from agents.content import ContentAgent
    config = await get_tenant_config(tenant_id)
    return ContentAgent(tenant_config=config)


async def get_social_agent(tenant_id: str):
    """Create a SocialAgent configured for the given tenant."""
    from agents.social import SocialAgent
    config = await get_tenant_config(tenant_id)
    return SocialAgent(tenant_config=config)


async def get_review_agent(tenant_id: str):
    """Create a ReviewAgent configured for the given tenant."""
    from agents.reviews import ReviewAgent
    config = await get_tenant_config(tenant_id)
    return ReviewAgent(tenant_config=config)


async def get_analytics_agent(tenant_id: str):
    """Create an AnalyticsAgent configured for the given tenant."""
    from agents.analytics import AnalyticsAgent
    config = await get_tenant_config(tenant_id)
    return AnalyticsAgent(tenant_config=config)
