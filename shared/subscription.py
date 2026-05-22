"""
Subscription / trial / admin-grant resolution.

Single source of truth for "does this tenant have access right now?". Reads
the subscription state stashed in user_settings.settings by the signup
trigger (migration 045), the Stripe webhook, and the admin grant endpoint.

See migration 045_subscriptions_and_trial.sql for the field layout. Plan
limits themselves still live in shared/usage.py (PLANS dict); this module
only decides whether the limits should apply or every billable action
should be blocked outright.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from shared.database import get_supabase, run_db

logger = logging.getLogger(__name__)


# Statuses that grant the user normal app access. "trial" is gated by
# trial_ends_at — once it passes the resolver downgrades to "expired".
ACTIVE_STATUSES = {"active", "trialing", "trial", "admin_granted"}
PAYMENT_GRACE_STATUSES = {"past_due"}  # still has access but UI nags


@dataclass(frozen=True)
class AccessStatus:
    """What the rest of the app needs to know to gate billable actions."""

    plan: str                       # always "site" with per-site pricing
    status: str                     # see ACTIVE_STATUSES above + canceled/expired
    has_access: bool                # False -> block billable actions
    trial_ends_at: Optional[str]    # ISO timestamp or None
    trial_days_remaining: int       # 0 when not on trial / trial over
    admin_granted_until: Optional[str]
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    blocked_reason: Optional[str]   # filled when has_access is False
    current_period_end: Optional[str] = None
    # Per-site pricing: how many sites this user has + Stripe quantity.
    site_count: int = 0
    quantity: int = 0               # mirror of Stripe sub quantity
    unit_price_usd: int = 0         # display copy
    monthly_total_usd: int = 0      # quantity * unit_price_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan": self.plan,
            "status": self.status,
            "has_access": self.has_access,
            "trial_ends_at": self.trial_ends_at,
            "trial_days_remaining": self.trial_days_remaining,
            "admin_granted_until": self.admin_granted_until,
            "stripe_customer_id": self.stripe_customer_id,
            "stripe_subscription_id": self.stripe_subscription_id,
            "blocked_reason": self.blocked_reason,
            "current_period_end": self.current_period_end,
            "site_count": self.site_count,
            "quantity": self.quantity,
            "unit_price_usd": self.unit_price_usd,
            "monthly_total_usd": self.monthly_total_usd,
        }


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        # Postgres trigger writes "...Z"; the dashboard writes either Z or +00:00.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve(settings: Dict[str, Any], site_count: int = 0) -> AccessStatus:
    """Decide access purely from a settings dict — no DB calls.

    Kept separate from ``get_access_status`` so the Stripe webhook can reuse
    the same precedence rules when projecting the new state. ``site_count``
    is supplied separately because computing it requires a DB call.
    """
    from shared.config import settings as cfg

    plan = (settings.get("plan") or "site").lower()
    raw_status = (settings.get("plan_status") or "trial").lower()
    trial_ends_at = settings.get("trial_ends_at")
    trial_dt = _parse_iso(trial_ends_at)
    stripe_customer_id = settings.get("stripe_customer_id")
    stripe_subscription_id = settings.get("stripe_subscription_id")
    admin_until = settings.get("admin_granted_until")
    admin_until_dt = _parse_iso(admin_until)
    current_period_end = settings.get("subscription_current_period_end")
    quantity = int(settings.get("subscription_quantity") or max(site_count, 1))
    unit_price = int(cfg.SITE_PRICE_USD or 0)
    monthly_total = quantity * unit_price
    now = _now()

    def _base(**overrides: Any) -> AccessStatus:
        defaults: Dict[str, Any] = dict(
            plan=plan,
            status=raw_status,
            has_access=False,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=0,
            admin_granted_until=admin_until,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            blocked_reason=None,
            current_period_end=current_period_end,
            site_count=site_count,
            quantity=quantity,
            unit_price_usd=unit_price,
            monthly_total_usd=monthly_total,
        )
        defaults.update(overrides)
        return AccessStatus(**defaults)

    # 1) Admin grant overrides everything else. A null admin_granted_until
    # means unlimited; otherwise it expires.
    if raw_status == "admin_granted":
        if admin_until is None or (admin_until_dt and admin_until_dt > now):
            return _base(status="admin_granted", has_access=True)
        # grant expired — fall through to subscription/trial checks below

    # 2) Active Stripe subscription wins next.
    if raw_status in ("active", "trialing"):
        return _base(status=raw_status, has_access=True)

    # 3) past_due — Stripe is retrying the card. Keep access for the
    # current period so an autopay hiccup doesn't lock the user out, but
    # surface the state to the UI.
    if raw_status == "past_due":
        return _base(status="past_due", has_access=True)

    # 4) Trial — has access until trial_ends_at, blocked after.
    if raw_status == "trial":
        if trial_dt and trial_dt > now:
            secs_left = (trial_dt - now).total_seconds()
            days_left = max(1, int(secs_left // 86400) + (1 if secs_left % 86400 else 0))
            return _base(status="trial", has_access=True, trial_days_remaining=days_left)
        # Trial ended → treat as expired below.

    # 5) canceled / expired / anything else → blocked.
    blocked_reason = "trial_expired" if raw_status == "trial" else raw_status or "no_subscription"
    return _base(
        status="expired" if raw_status == "trial" else (raw_status or "expired"),
        has_access=False,
        blocked_reason=blocked_reason,
    )


async def count_user_sites(user_id: str) -> int:
    """Return the number of sites under ``user_id`` (the per-site billing key).

    Falls back to 1 for users with no user_sites row so checkout never tries
    to charge for zero seats.
    """
    if not user_id or user_id == "default":
        return 1

    def _fetch():
        sb = get_supabase()
        return (
            sb.table("user_sites")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
    try:
        res = await run_db(_fetch)
        n = int(getattr(res, "count", None) or len(res.data or []))
        return max(n, 1)
    except Exception as e:
        logger.debug("count_user_sites failed for %s: %s", user_id, e)
        return 1


async def get_settings_row(user_id: str) -> Dict[str, Any]:
    """Return the raw settings JSON for ``user_id`` (empty dict on miss)."""
    def _fetch():
        sb = get_supabase()
        return (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    try:
        res = await run_db(_fetch)
        return (res.data or {}).get("settings") or {}
    except Exception as e:
        logger.debug("get_settings_row failed for %s: %s", user_id, e)
        return {}


async def _resolve_site_owner(site_id: str) -> Optional[str]:
    """If site_id is a UUID in user_sites, return the owner's user_id."""
    def _fetch():
        sb = get_supabase()
        return (
            sb.table("user_sites")
            .select("user_id")
            .eq("id", site_id)
            .maybe_single()
            .execute()
        )
    try:
        res = await run_db(_fetch)
        return (res.data or {}).get("user_id")
    except Exception as e:
        logger.debug("_resolve_site_owner failed for %s: %s", site_id, e)
        return None


async def get_access_status(user_id: str) -> AccessStatus:
    """Resolve the subscription state for ``user_id``."""
    if not user_id or user_id == "default":
        # Internal/system callers (e.g. scheduled jobs without a tenant) skip the gate.
        return AccessStatus(
            plan="site",
            status="admin_granted",
            has_access=True,
            trial_ends_at=None,
            trial_days_remaining=0,
            admin_granted_until=None,
            stripe_customer_id=None,
            stripe_subscription_id=None,
            blocked_reason=None,
        )
    settings = await get_settings_row(user_id)
    # When no settings row is found the caller may have passed a site_id
    # (user_sites.id) rather than the auth user_id. The dashboard sends
    # X-Sama-Site-Id which TenantMiddleware promotes to tenant_id, so
    # check_and_increment ends up here with a site UUID. Resolve the site
    # owner and retry so admin-granted / active-subscription status is read
    # from the correct user_settings row.
    if not settings:
        owner_id = await _resolve_site_owner(user_id)
        if owner_id and owner_id != user_id:
            settings = await get_settings_row(owner_id)
            user_id = owner_id
    site_count = await count_user_sites(user_id)
    return _resolve(settings, site_count=site_count)


async def update_settings(user_id: str, patch: Dict[str, Any]) -> None:
    """Shallow-merge ``patch`` into user_settings.settings for ``user_id``.

    The Stripe webhook and admin grant routes call this — the dashboard
    never writes subscription state directly.
    """
    if not user_id:
        return

    def _exec():
        sb = get_supabase()
        existing = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        current = (existing.data or {}).get("settings") or {}
        merged = {**current, **patch}
        return (
            sb.table("user_settings")
            .upsert(
                {
                    "user_id": user_id,
                    "settings": merged,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="user_id",
            )
            .execute()
        )

    try:
        await run_db(_exec)
    except Exception as e:
        logger.exception("update_settings failed for %s: %s", user_id, e)
        raise
