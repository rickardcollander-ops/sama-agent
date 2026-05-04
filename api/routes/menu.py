"""
Menu API — drives the dashboard's top navigation.

Returns one item per agent with metadata (label, route, icon) and an
``enabled`` flag pulled from ``tenant_agent_config``. The frontend
filters out disabled items so toggling an agent off in settings hides
it from the top menu.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


# Single source of truth for menu metadata. Order here = order in the UI.
MENU_ITEMS: List[Dict[str, Any]] = [
    {"id": "strategy",  "label": "Strategi",     "route": "/strategy",      "icon": "compass",     "default_enabled": True},
    {"id": "seo",       "label": "SEO",          "route": "/seo",           "icon": "search",      "default_enabled": True},
    {"id": "content",   "label": "Innehåll",     "route": "/content",       "icon": "edit",        "default_enabled": True},
    {"id": "social",    "label": "Social",       "route": "/social",        "icon": "share",       "default_enabled": True},
    {"id": "ads",       "label": "Annonser",     "route": "/ads",           "icon": "megaphone",   "default_enabled": False},
    {"id": "reviews",   "label": "Recensioner",  "route": "/reviews",       "icon": "star",        "default_enabled": True},
    {"id": "analytics", "label": "Analys",       "route": "/analytics",     "icon": "bar-chart",   "default_enabled": True},
    {"id": "geo",       "label": "AI-synlighet", "route": "/ai-visibility", "icon": "sparkles",    "default_enabled": True},
]


@router.get("")
@router.get("/")
async def get_menu(request: Request):
    """Return menu items with per-tenant ``enabled`` state.

    Frontend should hide items where ``enabled`` is false — that's how
    "toggle in settings → hide from top menu" works end-to-end.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    enabled_map: Dict[str, bool] = {}
    try:
        result = (
            sb.table("tenant_agent_config")
            .select("agent_name,enabled")
            .eq("tenant_id", tenant_id)
            .execute()
        )
        for row in result.data or []:
            enabled_map[row["agent_name"]] = bool(row.get("enabled"))
    except Exception as e:
        logger.warning(f"[menu] could not load tenant_agent_config: {e}")

    items = []
    for item in MENU_ITEMS:
        items.append({
            **item,
            "enabled": enabled_map.get(item["id"], item["default_enabled"]),
        })

    return {
        "tenant_id": tenant_id,
        "items": items,
        "visible": [i for i in items if i["enabled"]],
    }
