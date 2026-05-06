"""
Analytics Debug API Route

Live probe of every analytics data source for the current tenant. Each
`_fetch_*_data` method on AnalyticsAgent already returns a status dict
describing what the upstream API responded — this endpoint exposes those
dicts directly so an operator (or the dashboard) can see exactly what
GSC, GA4, Ads, Reviews and Content are returning RIGHT NOW, without
waiting on a daily_metrics upsert cycle.

This is a read-only probe: it does not touch daily_metrics at all.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from agents.analytics import AnalyticsAgent
from shared.tenant import get_tenant_config

router = APIRouter()
logger = logging.getLogger(__name__)


async def _safe_call(name: str, coro_factory) -> Dict[str, Any]:
    """Run a fetcher and turn any exception into a structured error dict."""
    try:
        result = await coro_factory()
        if not isinstance(result, dict):
            return {"status": "error", "error": f"fetcher returned {type(result).__name__}, not dict"}
        return result
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


@router.get("/probe")
async def probe(request: Request):
    """
    Run every analytics fetcher live and return their raw responses, plus
    the tenant config that drove them. Useful for diagnosing why dashboard
    metrics are 0 — the answer is almost always in one of these dicts.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = await get_tenant_config(tenant_id)
    agent = AnalyticsAgent(tenant_config=config)

    seo = await _safe_call("seo", agent._fetch_seo_data)
    ads = await _safe_call("ads", agent._fetch_ads_data)
    reviews = await _safe_call("reviews", agent._fetch_reviews_data)
    content = await _safe_call("content", agent._fetch_content_data)
    ga4 = await _safe_call("ga4", agent._fetch_ga4_data)

    return {
        "tenant_id": tenant_id,
        "tenant_config": {
            "domain": getattr(config, "domain", None),
            "site_url": getattr(config, "site_url", None),
            "gsc_site_url": getattr(config, "gsc_site_url", None),
            "ga4_property_id": getattr(config, "ga4_property_id", None) or None,
        },
        "channels": {
            "seo": seo,
            "google_ads": ads,
            "reviews": reviews,
            "content": content,
            "ga4": ga4,
        },
    }
