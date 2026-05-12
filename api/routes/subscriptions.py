"""
Stripe subscription routes + admin free-access grant.

Flow
----
1. New user signs up. The auth trigger (migration 045) writes a 3-day trial
   into user_settings.settings ({plan_status: "trial", trial_ends_at: ...}).
2. While on trial / paid plan they use the app normally.
3. ``GET /api/subscriptions/status`` returns the current state for the UI
   (plan, status, trial_days_remaining, blocked_reason).
4. ``POST /api/subscriptions/checkout`` opens a Stripe Checkout session for
   the chosen tier and returns the hosted URL.
5. Stripe posts back to ``/api/subscriptions/webhook``. We update the
   user_settings JSON via shared.subscription.update_settings.
6. ``POST /api/subscriptions/portal`` returns a Stripe Billing Portal URL
   so paying customers can manage / cancel their subscription.

Admin overrides
---------------
* ``POST /api/subscriptions/admin/grant`` — give a target user free access
  (optionally until a date). Locked to the ADMIN_EMAIL config.
* ``POST /api/subscriptions/admin/revoke`` — flip them back to expired.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.config import settings as cfg
from shared.database import get_supabase, run_db
from shared.subscription import (
    AccessStatus,
    get_access_status,
    get_settings_row,
    update_settings,
)
from shared.webhook_verify import verify_stripe

logger = logging.getLogger(__name__)

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

PRICE_BY_PLAN = {
    "starter": "STRIPE_PRICE_STARTER",
    "growth": "STRIPE_PRICE_GROWTH",
    "enterprise": "STRIPE_PRICE_ENTERPRISE",
}


def _stripe():
    """Return the configured stripe SDK module, or raise 503."""
    if not cfg.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")
    import stripe  # type: ignore

    stripe.api_key = cfg.STRIPE_SECRET_KEY
    return stripe


def _success_url() -> str:
    return cfg.STRIPE_SUCCESS_URL or f"{cfg.DASHBOARD_BASE_URL.rstrip('/')}/c/settings/billing?status=success&session_id={{CHECKOUT_SESSION_ID}}"


def _cancel_url() -> str:
    return cfg.STRIPE_CANCEL_URL or f"{cfg.DASHBOARD_BASE_URL.rstrip('/')}/c/pricing?status=cancel"


def _resolve_price_id(plan: str) -> str:
    plan = plan.lower().strip()
    key = PRICE_BY_PLAN.get(plan)
    if not key:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")
    price_id = getattr(cfg, key, "") or ""
    if not price_id:
        raise HTTPException(status_code=503, detail=f"Stripe price not configured for plan '{plan}'")
    return price_id


def _require_user(request: Request) -> str:
    """Return the authenticated user_id (== account_id). 401 when missing."""
    user_id = getattr(request.state, "account_id", None) or getattr(request.state, "tenant_id", None)
    if not user_id or user_id == "default":
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def _lookup_email(user_id: str) -> Optional[str]:
    """Fetch the Supabase auth email for ``user_id``. Never raises."""
    try:
        sb = get_supabase()
        user = sb.auth.admin.get_user_by_id(user_id)  # type: ignore[attr-defined]
        return getattr(getattr(user, "user", None), "email", None)
    except Exception as e:
        logger.debug("auth.admin.get_user_by_id(%s) failed: %s", user_id, e)
        return None


def _require_admin(request: Request) -> str:
    """Return the admin's user_id, 403 when caller is not the admin."""
    user_id = _require_user(request)
    email = _lookup_email(user_id)
    if not email or email.strip().lower() != cfg.ADMIN_EMAIL.strip().lower():
        raise HTTPException(status_code=403, detail="Admin only")
    return user_id


# ── pydantic models ──────────────────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    plan: str = Field(..., description="starter | growth | enterprise")


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


class AdminGrantRequest(BaseModel):
    user_id: str
    plan: str = "growth"
    granted_until: Optional[str] = None  # ISO timestamp; null = unlimited
    note: Optional[str] = None


class AdminRevokeRequest(BaseModel):
    user_id: str
    note: Optional[str] = None


# ── routes ───────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(request: Request) -> Dict[str, Any]:
    """Return the current subscription state for the authenticated user."""
    user_id = _require_user(request)
    status: AccessStatus = await get_access_status(user_id)
    return status.to_dict()


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(body: CheckoutRequest, request: Request) -> CheckoutResponse:
    """Create a Stripe Checkout session for ``body.plan`` and return its URL.

    Reuses the stored stripe_customer_id when present so all charges land on
    the same customer in Stripe. The 3-day trial is granted via signup, not
    via Stripe's own ``trial_period_days`` — paying users start billing at
    once. Existing trial users who haven't paid yet still see a Checkout
    flow with subscription_data.trial_end=trial_ends_at so Stripe matches
    the in-app countdown.
    """
    user_id = _require_user(request)
    price_id = _resolve_price_id(body.plan)
    stripe = _stripe()

    existing = await get_settings_row(user_id)
    customer_id = existing.get("stripe_customer_id")
    email = _lookup_email(user_id)

    subscription_data: Dict[str, Any] = {"metadata": {"user_id": user_id}}
    # If the user is still inside the in-app trial, honour it in Stripe too.
    if existing.get("plan_status") == "trial":
        trial_ends_at = existing.get("trial_ends_at")
        try:
            if trial_ends_at:
                dt = datetime.fromisoformat(trial_ends_at.replace("Z", "+00:00"))
                if dt > datetime.now(timezone.utc):
                    subscription_data["trial_end"] = int(dt.timestamp())
        except ValueError:
            pass

    session_kwargs: Dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": _success_url(),
        "cancel_url": _cancel_url(),
        "client_reference_id": user_id,
        "metadata": {"user_id": user_id, "plan": body.plan.lower()},
        "subscription_data": subscription_data,
        "allow_promotion_codes": True,
    }
    if customer_id:
        session_kwargs["customer"] = customer_id
    elif email:
        session_kwargs["customer_email"] = email

    try:
        session = await run_db(lambda: stripe.checkout.Session.create(**session_kwargs))
    except Exception as e:
        logger.exception("Stripe checkout.Session.create failed for %s: %s", user_id, e)
        raise HTTPException(status_code=502, detail="Stripe checkout failed")

    if not session.url:  # defensive — should never happen
        raise HTTPException(status_code=502, detail="Stripe returned no checkout URL")
    return CheckoutResponse(url=session.url)


@router.post("/portal", response_model=PortalResponse)
async def create_portal(request: Request) -> PortalResponse:
    """Return a Stripe Billing Portal URL for the current customer."""
    user_id = _require_user(request)
    existing = await get_settings_row(user_id)
    customer_id = existing.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer yet — subscribe first")

    stripe = _stripe()
    try:
        portal = await run_db(
            lambda: stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=f"{cfg.DASHBOARD_BASE_URL.rstrip('/')}/c/settings/billing",
            )
        )
    except Exception as e:
        logger.exception("Stripe billing_portal.Session.create failed: %s", e)
        raise HTTPException(status_code=502, detail="Stripe portal failed")
    return PortalResponse(url=portal.url)


# ── webhook ──────────────────────────────────────────────────────────────────


def _plan_from_price(price_id: Optional[str]) -> Optional[str]:
    if not price_id:
        return None
    if price_id == cfg.STRIPE_PRICE_STARTER:
        return "starter"
    if price_id == cfg.STRIPE_PRICE_GROWTH:
        return "growth"
    if price_id == cfg.STRIPE_PRICE_ENTERPRISE:
        return "enterprise"
    return None


def _user_id_from_event(obj: Dict[str, Any]) -> Optional[str]:
    """Pull user_id out of the various places Stripe stashes our metadata."""
    meta = obj.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("user_id"):
        return meta["user_id"]
    sub_data = obj.get("subscription_details") or {}
    if isinstance(sub_data, dict):
        sm = sub_data.get("metadata") or {}
        if isinstance(sm, dict) and sm.get("user_id"):
            return sm["user_id"]
    if obj.get("client_reference_id"):
        return obj["client_reference_id"]
    return None


def _iso(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


async def _handle_subscription_event(sub: Dict[str, Any]) -> None:
    """Project a customer.subscription.* event onto user_settings."""
    user_id = _user_id_from_event(sub)
    customer_id = sub.get("customer")

    # When the event has no metadata (older subscriptions) recover via the
    # customer id we stored on checkout.
    if not user_id and customer_id:
        def _lookup():
            return (
                get_supabase()
                .table("user_settings")
                .select("user_id, settings")
                .contains("settings", {"stripe_customer_id": customer_id})
                .limit(1)
                .execute()
            )
        try:
            res = await run_db(_lookup)
            rows = res.data or []
            if rows:
                user_id = rows[0]["user_id"]
        except Exception as e:
            logger.debug("customer->user lookup failed: %s", e)

    if not user_id:
        logger.warning("Stripe subscription event with no resolvable user_id: id=%s", sub.get("id"))
        return

    items = (sub.get("items") or {}).get("data") or []
    price_id = items[0].get("price", {}).get("id") if items else None
    plan = _plan_from_price(price_id)

    status = sub.get("status") or "active"  # active|trialing|past_due|canceled|unpaid|incomplete...
    status_map = {
        "active": "active",
        "trialing": "trialing",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "past_due",
        "incomplete": "trial",       # waiting on first payment — keep trial UI
        "incomplete_expired": "expired",
        "paused": "canceled",
    }
    mapped = status_map.get(status, status)

    patch: Dict[str, Any] = {
        "plan_status": mapped,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": sub.get("id"),
        "subscription_current_period_end": _iso(sub.get("current_period_end")),
    }
    if plan:
        patch["plan"] = plan
    await update_settings(user_id, patch)
    logger.info("Stripe sub %s -> user_settings(%s) status=%s plan=%s", sub.get("id"), user_id, mapped, plan)


async def _handle_checkout_completed(session: Dict[str, Any]) -> None:
    """Persist the customer id as soon as checkout completes."""
    user_id = _user_id_from_event(session)
    if not user_id:
        return
    patch = {
        "stripe_customer_id": session.get("customer"),
        "stripe_subscription_id": session.get("subscription"),
    }
    # plan_status will be set authoritatively by the subscription.* event
    # that follows; we just make sure the customer id is recorded.
    await update_settings(user_id, {k: v for k, v in patch.items() if v})


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Dict[str, str]:
    """Stripe → SAMA webhook.

    Verified with the shared HMAC helper rather than ``stripe.Webhook.construct_event``
    so the route never raises on a missing SDK install, but the validation
    semantics (t=…,v1=… with 5 min tolerance) are identical.
    """
    if not cfg.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Stripe webhook secret not configured")

    body = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not verify_stripe(secret=cfg.STRIPE_WEBHOOK_SECRET, body=body, signature_header=sig):
        raise HTTPException(status_code=401, detail="Invalid Stripe signature")

    try:
        import json
        event = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(obj)
        elif event_type.startswith("customer.subscription."):
            await _handle_subscription_event(obj)
        elif event_type == "invoice.payment_failed":
            user_id = _user_id_from_event(obj)
            if user_id:
                await update_settings(user_id, {"plan_status": "past_due"})
        # Other events are acknowledged but ignored.
    except Exception as e:
        # Never 500 to Stripe — they retry aggressively. Log + ack.
        logger.exception("webhook handler failed for %s: %s", event_type, e)

    return {"received": "ok"}


# ── admin grant ──────────────────────────────────────────────────────────────


@router.post("/admin/grant")
async def admin_grant(body: AdminGrantRequest, request: Request) -> Dict[str, Any]:
    """Give a target user free access. Idempotent (overwrites existing grant)."""
    admin_id = _require_admin(request)
    admin_email = _lookup_email(admin_id) or cfg.ADMIN_EMAIL

    plan = body.plan.lower().strip() or "growth"
    if plan not in ("starter", "growth", "enterprise"):
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

    granted_until_dt: Optional[datetime] = None
    if body.granted_until:
        try:
            granted_until_dt = datetime.fromisoformat(body.granted_until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="granted_until must be ISO 8601 or null")

    patch = {
        "plan": plan,
        "plan_status": "admin_granted",
        "admin_granted_until": granted_until_dt.isoformat() if granted_until_dt else None,
        "admin_granted_by": admin_email,
        "admin_granted_at": datetime.now(timezone.utc).isoformat(),
    }
    await update_settings(body.user_id, patch)

    # Audit row
    try:
        def _log():
            return (
                get_supabase()
                .table("subscription_admin_grants")
                .insert(
                    {
                        "user_id": body.user_id,
                        "action": "grant",
                        "granted_until": patch["admin_granted_until"],
                        "admin_email": admin_email,
                        "note": body.note,
                    }
                )
                .execute()
            )
        await run_db(_log)
    except Exception as e:
        logger.warning("admin grant audit log failed: %s", e)

    status = await get_access_status(body.user_id)
    return {"ok": True, "status": status.to_dict()}


@router.post("/admin/revoke")
async def admin_revoke(body: AdminRevokeRequest, request: Request) -> Dict[str, Any]:
    """Revoke a previously granted free access (back to expired)."""
    admin_id = _require_admin(request)
    admin_email = _lookup_email(admin_id) or cfg.ADMIN_EMAIL

    # Only flip plan_status if we're the ones holding the seat. Don't trample
    # a real Stripe subscription that landed in between.
    existing = await get_settings_row(body.user_id)
    if existing.get("plan_status") != "admin_granted":
        return {"ok": True, "status": (await get_access_status(body.user_id)).to_dict(), "note": "not_admin_granted"}

    patch = {
        "plan_status": "expired",
        "admin_granted_until": None,
        "admin_granted_by": None,
    }
    await update_settings(body.user_id, patch)

    try:
        def _log():
            return (
                get_supabase()
                .table("subscription_admin_grants")
                .insert(
                    {
                        "user_id": body.user_id,
                        "action": "revoke",
                        "granted_until": None,
                        "admin_email": admin_email,
                        "note": body.note,
                    }
                )
                .execute()
            )
        await run_db(_log)
    except Exception as e:
        logger.warning("admin revoke audit log failed: %s", e)

    status = await get_access_status(body.user_id)
    return {"ok": True, "status": status.to_dict()}


@router.get("/admin/grants")
async def admin_list_grants(request: Request, limit: int = 100) -> Dict[str, Any]:
    """Return recent grant/revoke entries (audit trail)."""
    _require_admin(request)
    limit = max(1, min(limit, 500))

    def _fetch():
        return (
            get_supabase()
            .table("subscription_admin_grants")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    try:
        res = await run_db(_fetch)
        return {"grants": res.data or []}
    except Exception as e:
        logger.warning("admin_list_grants failed: %s", e)
        return {"grants": []}
