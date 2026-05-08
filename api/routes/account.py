"""
Account API — workspace/team member listing.

The dashboard's settings page calls ``GET /api/account/members`` to render
who has access to the current workspace. SAMA is single-user today, so the
endpoint reports the authenticated account holder as the sole owner. The
shape is deliberately list-friendly so a real multi-member implementation
can land later without breaking the frontend contract.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request

from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


def _lookup_user_email(user_id: str) -> Optional[str]:
    """Best-effort email lookup via the Supabase admin API. Never raises.

    Anything that goes wrong — Supabase env not set (``get_supabase`` raises
    RuntimeError), anon key in use (admin call rejected), network blip, or an
    unexpected client-library exception — is swallowed and reported as None.
    The dashboard renders fine without an email; a 500 on this endpoint is
    much worse than a missing display name.
    """
    try:
        sb = get_supabase()
    except Exception as e:
        logger.debug("get_supabase failed during member lookup: %s", e)
        return None
    if not sb:
        return None
    try:
        user = sb.auth.admin.get_user_by_id(user_id)  # type: ignore[attr-defined]
        return getattr(getattr(user, "user", None), "email", None)
    except Exception as e:
        logger.debug("auth.admin.get_user_by_id failed for %s: %s", user_id, e)
        return None


@router.get("/members")
async def list_members(request: Request):
    """Return the members of the active account.

    SAMA is single-tenant per account today, so this returns one entry — the
    authenticated account holder — with role ``owner``. Anonymous callers get
    an empty list rather than a 500 so the dashboard renders cleanly. Any
    unexpected error is degraded to an empty list for the same reason — a
    500 here lights up the browser console and blocks the settings view.
    """
    try:
        account_id = getattr(request.state, "account_id", None)
        authenticated = getattr(request.state, "authenticated", False)

        if not account_id:
            return {"members": []}

        email = _lookup_user_email(account_id) if authenticated else None
        name = email.split("@", 1)[0] if email else None

        return {
            "members": [
                {
                    "id": account_id,
                    "email": email,
                    "name": name,
                    "role": "owner",
                }
            ]
        }
    except Exception as e:
        logger.exception("list_members failed: %s", e)
        return {"members": []}
