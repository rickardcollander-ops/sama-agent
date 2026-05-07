"""
Weekly status email API.

The dashboard calls these endpoints from the user's notification settings page
to (a) preview what their next email will look like and (b) send a one-off
test email to themselves. The actual scheduled batch is fired from
shared.scheduler.
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr

from shared.weekly_email import (
    preview_weekly_status_for_user,
    send_weekly_status_for_all,
    send_weekly_status_for_user,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class TestSendPayload(BaseModel):
    user_id: str
    recipient_override: EmailStr | None = None


class BatchSendPayload(BaseModel):
    # Reserved for future scoping (e.g. by tenant). Empty for now — the
    # endpoint always operates on every opted-in user.
    confirm: bool = False


@router.post("/weekly/test")
async def send_test(payload: TestSendPayload):
    """Send a one-off [TEST] weekly status email to the given user."""
    result = send_weekly_status_for_user(
        payload.user_id,
        recipient_override=payload.recipient_override,
        test=True,
    )
    if not result.get("sent") and not result.get("skipped"):
        raise HTTPException(status_code=500, detail=result.get("error") or "send_failed")
    return result


@router.get("/weekly/preview/{user_id}", response_class=HTMLResponse)
async def preview(user_id: str):
    """Render the weekly email HTML for a user without sending it.

    Returns text/html so the dashboard can show it in an iframe.
    """
    composed = preview_weekly_status_for_user(user_id)
    return HTMLResponse(content=composed["html"])


@router.post("/weekly/run-now")
async def run_now(payload: BatchSendPayload):
    """Manual trigger of the full weekly batch — admin-only escape hatch.

    Requires `confirm: true` in the body to make accidental fires harder.
    """
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="confirm: true required")
    return send_weekly_status_for_all()
