"""
Webhook Handlers
Receives webhooks from external services (Cal.com, Brevo, etc.)
and routes them into the SAMA event bus.
"""

from fastapi import APIRouter, Request, HTTPException
from typing import Dict, Any
import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/calcom")
async def calcom_webhook(request: Request):
    """
    Cal.com webhook handler.
    Triggered when a meeting is booked, cancelled, or rescheduled.
    See: https://cal.com/docs/api-reference/webhooks
    """
    # Verify webhook signature if secret is configured
    from shared.config import settings
    secret = settings.CALCOM_WEBHOOK_SECRET
    if secret:
        signature = request.headers.get("x-cal-signature-256", "")
        body = await request.body()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    trigger = payload.get("triggerEvent", "")
    booking = payload.get("payload", {})

    logger.info(f"Cal.com webhook received: {trigger}")

    if trigger == "BOOKING_CREATED":
        attendees = booking.get("attendees", [])
        email = attendees[0].get("email", "") if attendees else ""
        name = attendees[0].get("name", "") if attendees else ""
        start_time = booking.get("startTime", "")
        event_type = booking.get("eventType", {}).get("title", "demo")

        if email:
            # Ensure lead exists
            try:
                from shared.database import get_supabase
                from datetime import datetime
                sb = get_supabase()

                existing = sb.table("leads").select("id").eq("email", email).execute()
                if not existing.data:
                    # Create lead from booking
                    result = sb.table("leads").insert({
                        "email": email,
                        "name": name,
                        "status": "meeting_booked",
                        "source_url": "cal.com",
                        "utm_source": "calcom",
                        "score": 80,  # Booking = high intent
                        "created_at": datetime.utcnow().isoformat(),
                    }).execute()
            except Exception as e:
                logger.error(f"Failed to create lead from booking: {e}")

            # Publish meeting_booked event
            try:
                from shared.event_bus_registry import get_event_bus
                bus = get_event_bus()
                if bus:
                    await bus.publish("meeting_booked", {
                        "email": email,
                        "name": name,
                        "start_time": start_time,
                        "event_type": event_type,
                        "booking_url": booking.get("metadata", {}).get("videoCallUrl", ""),
                    })
            except Exception as e:
                logger.error(f"Failed to publish meeting_booked event: {e}")

        return {"status": "ok", "trigger": trigger}

    elif trigger == "BOOKING_CANCELLED":
        attendees = booking.get("attendees", [])
        email = attendees[0].get("email", "") if attendees else ""
        if email:
            try:
                from shared.database import get_supabase
                from datetime import datetime
                sb = get_supabase()
                sb.table("leads").update({
                    "status": "contacted",
                    "updated_at": datetime.utcnow().isoformat(),
                }).eq("email", email).eq("status", "meeting_booked").execute()
            except Exception:
                pass

            try:
                from shared.notifications import notification_service
                await notification_service.notify(
                    title="Meeting cancelled",
                    message=f"{email} cancelled their booking",
                    severity="warning",
                    agent="leads",
                )
            except Exception:
                pass

        return {"status": "ok", "trigger": trigger}

    return {"status": "ignored", "trigger": trigger}


@router.post("/webhooks/brevo")
async def brevo_webhook(request: Request):
    """
    Brevo (Sendinblue) webhook handler.
    Tracks email opens, clicks, and unsubscribes.
    """
    payload = await request.json()
    event = payload.get("event", "")
    email = payload.get("email", "")

    logger.info(f"Brevo webhook: {event} for {email}")

    if event in ("click", "opened") and email:
        try:
            from shared.database import get_supabase
            from datetime import datetime
            sb = get_supabase()

            lead = sb.table("leads").select("id").eq("email", email).execute()
            if lead.data:
                lead_id = lead.data[0]["id"]
                sb.table("lead_touchpoints").insert({
                    "lead_id": lead_id,
                    "touchpoint_type": f"email_{event}",
                    "url": payload.get("link", ""),
                    "created_at": datetime.utcnow().isoformat(),
                }).execute()

                # Re-score lead with new touchpoint
                from shared.lead_scoring import score_lead
                new_score = await score_lead(lead_id)
                sb.table("leads").update({"score": new_score}).eq("id", lead_id).execute()

                from shared.lead_scoring import check_and_escalate
                await check_and_escalate(lead_id, new_score)
        except Exception as e:
            logger.error(f"Brevo webhook processing failed: {e}")

    return {"status": "ok"}
