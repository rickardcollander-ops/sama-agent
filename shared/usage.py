"""
Usage metering and plan-limit enforcement.

Counts billable events (content generated, ad creatives generated, agent runs,
review responses, AI tokens) per tenant per calendar month and enforces caps
defined by the tenant's plan.

Records live in the ``tenant_usage`` table:
    tenant_id, month (YYYY-MM-01 date), metric, count, updated_at

Plans are stored in ``user_settings.settings.plan`` (defaults to ``starter``).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Optional

from shared.database import get_supabase

logger = logging.getLogger(__name__)


# ── Plan definitions ─────────────────────────────────────────────────────────
# Match the tiers shown on the dashboard's /c/pricing page.

@dataclass(frozen=True)
class PlanLimits:
    name: str
    content_pieces: int
    ad_creatives: int
    agent_runs: int
    review_responses: int


PLANS: Dict[str, PlanLimits] = {
    "starter": PlanLimits(
        name="Starter",
        content_pieces=20,
        ad_creatives=10,
        agent_runs=200,
        review_responses=50,
    ),
    "growth": PlanLimits(
        name="Growth",
        content_pieces=100,
        ad_creatives=50,
        agent_runs=1000,
        review_responses=300,
    ),
    "enterprise": PlanLimits(
        name="Enterprise",
        # Effectively unlimited; we still record usage for billing visibility.
        content_pieces=10**9,
        ad_creatives=10**9,
        agent_runs=10**9,
        review_responses=10**9,
    ),
}

DEFAULT_PLAN = "starter"

# Known metrics — keep in sync with PlanLimits attributes.
METRICS = ("content_pieces", "ad_creatives", "agent_runs", "review_responses")


class UsageLimitExceeded(Exception):
    """Raised when a tenant has hit a metered cap on their current plan."""

    def __init__(self, metric: str, plan: str, limit: int, current: int):
        self.metric = metric
        self.plan = plan
        self.limit = limit
        self.current = current
        super().__init__(
            f"Plan limit reached: {metric} = {current}/{limit} on {plan} plan"
        )


# ── Plan resolution ──────────────────────────────────────────────────────────

async def get_tenant_plan(tenant_id: str) -> PlanLimits:
    if tenant_id == "default":
        return PLANS["enterprise"]
    try:
        sb = get_supabase()
        res = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        plan_key = (res.data or {}).get("settings", {}).get("plan") or DEFAULT_PLAN
    except Exception as e:
        logger.debug(f"Plan lookup failed for {tenant_id}: {e}; using default")
        plan_key = DEFAULT_PLAN
    return PLANS.get(plan_key, PLANS[DEFAULT_PLAN])


# ── Counter helpers ──────────────────────────────────────────────────────────

def _current_month() -> str:
    today = datetime.now(timezone.utc).date()
    return date(today.year, today.month, 1).isoformat()


async def get_usage(tenant_id: str, metric: str, month: Optional[str] = None) -> int:
    if metric not in METRICS:
        raise ValueError(f"Unknown metric: {metric}")
    month = month or _current_month()
    try:
        sb = get_supabase()
        res = (
            sb.table("tenant_usage")
            .select("count")
            .eq("tenant_id", tenant_id)
            .eq("month", month)
            .eq("metric", metric)
            .maybe_single()
            .execute()
        )
        return int((res.data or {}).get("count", 0))
    except Exception:
        return 0


async def increment_usage(tenant_id: str, metric: str, by: int = 1) -> int:
    """Increment the counter and return the new value. Best-effort — never raises."""
    if metric not in METRICS:
        raise ValueError(f"Unknown metric: {metric}")
    if tenant_id == "default":
        return 0
    month = _current_month()
    try:
        sb = get_supabase()
        current = await get_usage(tenant_id, metric, month)
        new_value = current + by
        sb.table("tenant_usage").upsert(
            {
                "tenant_id": tenant_id,
                "month": month,
                "metric": metric,
                "count": new_value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="tenant_id,month,metric",
        ).execute()
        return new_value
    except Exception as e:
        logger.warning(f"increment_usage failed ({tenant_id}/{metric}): {e}")
        return 0


async def check_and_increment(tenant_id: str, metric: str, by: int = 1) -> int:
    """
    Enforce the plan limit for ``metric`` and increment when within budget.
    Raises UsageLimitExceeded when the new value would exceed the cap.
    """
    if tenant_id == "default":
        return 0
    plan = await get_tenant_plan(tenant_id)
    limit = getattr(plan, metric)
    current = await get_usage(tenant_id, metric)
    if current + by > limit:
        raise UsageLimitExceeded(metric, plan.name.lower(), limit, current)
    return await increment_usage(tenant_id, metric, by=by)


async def get_usage_summary(tenant_id: str) -> Dict[str, Dict[str, int]]:
    """Return current-month usage and limits for the tenant, keyed by metric."""
    plan = await get_tenant_plan(tenant_id)
    out: Dict[str, Dict[str, int]] = {"plan": {"name": plan.name}}
    for metric in METRICS:
        out[metric] = {
            "used": await get_usage(tenant_id, metric),
            "limit": getattr(plan, metric),
        }
    return out
