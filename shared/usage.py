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
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Optional

from shared.database import get_supabase, run_db

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


_SITE_LIMITS = PlanLimits(
    name="Site",
    content_pieces=100,
    ad_creatives=50,
    agent_runs=1000,
    review_responses=300,
)

PLANS: Dict[str, PlanLimits] = {
    # Single paid tier ($169/mo per site). plan="site" is what the Stripe
    # webhook + signup trigger write to user_settings.settings.plan.
    "site": _SITE_LIMITS,
    # Admin-granted bypass — effectively unlimited so comp / internal /
    # beta accounts never bump into the metered caps. Used by the
    # /api/admin/grant-free-access flow (it also flips plan_status to
    # 'admin_granted' which is what shared/subscription.py gates on).
    "free": PlanLimits(
        name="Free",
        content_pieces=10**9,
        ad_creatives=10**9,
        agent_runs=10**9,
        review_responses=10**9,
    ),
    # Legacy tier names kept as aliases so old user_settings.plan values
    # ("starter" / "growth" / "enterprise") still resolve while the
    # rollout settles.
    "starter": _SITE_LIMITS,
    "growth": _SITE_LIMITS,
    "enterprise": PlanLimits(
        name="Site",
        content_pieces=10**9,
        ad_creatives=10**9,
        agent_runs=10**9,
        review_responses=10**9,
    ),
}

DEFAULT_PLAN = "site"

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


class SubscriptionRequired(Exception):
    """Raised when a tenant's trial has expired and no active subscription exists.

    Different from UsageLimitExceeded so the API surface can map this to a
    distinct 402 Payment Required response that the dashboard handles by
    nudging the user to the pricing page.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Subscription required: {reason}")


# ── Plan resolution ──────────────────────────────────────────────────────────

# Short-TTL cache for the per-tenant plan. The plan only changes on
# upgrade/downgrade (rare), and a stale limit for up to a minute is harmless in
# either direction — so caching this read keeps it off the hot metered path
# (check_and_increment runs it on every billable action).
_plan_cache: Dict[str, "tuple[PlanLimits, float]"] = {}
_PLAN_CACHE_TTL_S = 60.0


async def get_tenant_plan(tenant_id: str) -> PlanLimits:
    if tenant_id == "default":
        return PLANS["enterprise"]
    cached = _plan_cache.get(tenant_id)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    try:
        sb = get_supabase()
        res = await run_db(lambda: (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        ))
        plan_key = (res.data or {}).get("settings", {}).get("plan") or DEFAULT_PLAN
    except Exception as e:
        logger.debug(f"Plan lookup failed for {tenant_id}: {e}; using default")
        plan_key = DEFAULT_PLAN
    plan = PLANS.get(plan_key, PLANS[DEFAULT_PLAN])
    _plan_cache[tenant_id] = (plan, now + _PLAN_CACHE_TTL_S)
    return plan


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
        res = await run_db(lambda: (
            sb.table("tenant_usage")
            .select("count")
            .eq("tenant_id", tenant_id)
            .eq("month", month)
            .eq("metric", metric)
            .maybe_single()
            .execute()
        ))
        return int((res.data or {}).get("count", 0))
    except Exception:
        return 0


async def increment_usage(
    tenant_id: str,
    metric: str,
    by: int = 1,
    known_current: Optional[int] = None,
) -> int:
    """Increment the counter and return the new value. Best-effort — never raises.

    ``known_current`` lets a caller that already read the current value (e.g.
    check_and_increment) skip the extra read here, saving a DB round-trip.
    """
    if metric not in METRICS:
        raise ValueError(f"Unknown metric: {metric}")
    if tenant_id == "default":
        return 0
    month = _current_month()
    try:
        sb = get_supabase()
        current = known_current if known_current is not None else await get_usage(tenant_id, metric, month)
        new_value = current + by
        await run_db(lambda: sb.table("tenant_usage").upsert(
            {
                "tenant_id": tenant_id,
                "month": month,
                "metric": metric,
                "count": new_value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="tenant_id,month,metric",
        ).execute())
        return new_value
    except Exception as e:
        logger.warning(f"increment_usage failed ({tenant_id}/{metric}): {e}")
        return 0


async def check_and_increment(tenant_id: str, metric: str, by: int = 1) -> int:
    """
    Enforce the plan limit for ``metric`` and increment when within budget.

    Raises ``SubscriptionRequired`` when the tenant's trial has expired and no
    active Stripe subscription / admin grant is in place — billable actions
    must not run for a user who hasn't paid.

    Raises ``UsageLimitExceeded`` when the new value would exceed the cap on
    the tenant's current plan.
    """
    if tenant_id == "default":
        return 0

    # Gate on access first — running the metered call for an expired trial
    # would waste an LLM token quota slot before the route can 402.
    from shared.subscription import get_access_status  # local import to avoid cycles

    access = await get_access_status(tenant_id)
    if not access.has_access:
        raise SubscriptionRequired(access.blocked_reason or "no_subscription")

    plan = await get_tenant_plan(tenant_id)
    limit = getattr(plan, metric)
    current = await get_usage(tenant_id, metric)
    if current + by > limit:
        raise UsageLimitExceeded(metric, plan.name.lower(), limit, current)
    # Reuse the count we just read instead of having increment_usage re-read it.
    return await increment_usage(tenant_id, metric, by=by, known_current=current)


async def get_usage_summary(tenant_id: str) -> Dict[str, Dict[str, int]]:
    """Return current-month usage, limits, and subscription state for the tenant."""
    plan = await get_tenant_plan(tenant_id)
    out: Dict[str, Dict[str, int]] = {"plan": {"name": plan.name}}
    for metric in METRICS:
        out[metric] = {
            "used": await get_usage(tenant_id, metric),
            "limit": getattr(plan, metric),
        }
    try:
        from shared.subscription import get_access_status  # local import
        access = await get_access_status(tenant_id)
        out["subscription"] = access.to_dict()  # type: ignore[assignment]
    except Exception as e:
        logger.debug("usage subscription enrich failed for %s: %s", tenant_id, e)
    return out
