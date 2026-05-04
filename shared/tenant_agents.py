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


async def get_ai_visibility_agent(tenant_id: str):
    """Create an AIVisibilityAgent (GEO monitor) configured for the given tenant."""
    from agents.ai_visibility import AIVisibilityAgent
    config = await get_tenant_config(tenant_id)
    return AIVisibilityAgent(tenant_config=config)


async def get_ads_agent(tenant_id: str):
    """Create a GoogleAdsAgent configured for the given tenant."""
    from agents.ads import GoogleAdsAgent
    config = await get_tenant_config(tenant_id)
    return GoogleAdsAgent(tenant_config=config)


async def get_strategy_agent(tenant_id: str):
    """Create a StrategyAgent configured for the given tenant."""
    from agents.strategy import StrategyAgent
    config = await get_tenant_config(tenant_id)
    return StrategyAgent(tenant_config=config)


# Map of agent_name → factory. Used by the scheduler and trigger endpoint to
# avoid the agent_name → factory if/elif ladder.
AGENT_FACTORIES = {
    "seo": get_seo_agent,
    "content": get_content_agent,
    "social": get_social_agent,
    "reviews": get_review_agent,
    "analytics": get_analytics_agent,
    "geo": get_ai_visibility_agent,
    "ads": get_ads_agent,
    "strategy": get_strategy_agent,
}


async def get_agent(agent_name: str, tenant_id: str):
    """Generic factory: returns a tenant-scoped instance of any registered agent."""
    factory = AGENT_FACTORIES.get(agent_name)
    if not factory:
        raise ValueError(f"Unknown agent: {agent_name}")
    return await factory(tenant_id)
