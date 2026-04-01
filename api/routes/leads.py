"""
Lead Capture & Management API
Endpoints for capturing leads from SAMA-generated content,
tracking touchpoints, and managing the lead pipeline.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class LeadCaptureRequest(BaseModel):
    email: str
    name: Optional[str] = ""
    company: Optional[str] = ""
    phone: Optional[str] = ""
    message: Optional[str] = ""
    source_url: Optional[str] = ""
    utm_source: Optional[str] = ""
    utm_medium: Optional[str] = ""
    utm_campaign: Optional[str] = ""
    utm_content: Optional[str] = ""


# ── Lead Capture ────────────────────────────────────────────────────────────

@router.post("/leads/capture")
async def capture_lead(request: Request):
    """
    Capture a lead from a form submission.
    Accepts both JSON and form-encoded data (for HTML form action).
    """
    # Parse body — support both JSON and form data
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    email = data.get("email", "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")

    try:
        from shared.database import get_supabase
        sb = get_supabase()

        # Check for existing lead with same email
        existing = sb.table("leads").select("id,score,status").eq("email", email).execute()

        lead_data = {
            "email": email,
            "name": data.get("name", ""),
            "company": data.get("company", ""),
            "phone": data.get("phone", ""),
            "message": data.get("message", ""),
            "source_url": data.get("source_url", ""),
            "utm_source": data.get("utm_source", ""),
            "utm_medium": data.get("utm_medium", ""),
            "utm_campaign": data.get("utm_campaign", ""),
            "utm_content": data.get("utm_content", ""),
            "status": "new",
            "score": 0,
            "created_at": datetime.utcnow().isoformat(),
        }

        if existing.data:
            # Update existing lead — keep higher score, update touchpoint
            lead_id = existing.data[0]["id"]
            sb.table("leads").update({
                "name": data.get("name") or existing.data[0].get("name", ""),
                "company": data.get("company", ""),
                "message": data.get("message", ""),
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", lead_id).execute()
        else:
            # Create new lead
            result = sb.table("leads").insert(lead_data).execute()
            lead_id = result.data[0]["id"] if result.data else None

        # Record touchpoint
        if lead_id:
            sb.table("lead_touchpoints").insert({
                "lead_id": lead_id,
                "touchpoint_type": "form_submission",
                "url": data.get("source_url", ""),
                "utm_source": data.get("utm_source", ""),
                "utm_medium": data.get("utm_medium", ""),
                "utm_campaign": data.get("utm_campaign", ""),
                "created_at": datetime.utcnow().isoformat(),
            }).execute()

        # Score the lead
        try:
            from shared.lead_scoring import score_lead
            score = await score_lead(lead_id)
            sb.table("leads").update({"score": score}).eq("id", lead_id).execute()
        except Exception as e:
            logger.warning(f"Lead scoring failed: {e}")

        # Publish lead_captured event
        try:
            from shared.event_bus_registry import get_event_bus
            bus = get_event_bus()
            if bus:
                await bus.publish("lead_captured", "sama_leads", {
                    "lead_id": lead_id,
                    "email": email,
                    "company": data.get("company", ""),
                    "source_url": data.get("source_url", ""),
                    "utm_source": data.get("utm_source", ""),
                })
        except Exception:
            pass

        # Send notification
        try:
            from shared.notifications import notification_service
            await notification_service.notify(
                title="New lead captured!",
                message=f"{email} ({data.get('company', 'Unknown')}) via {data.get('source_url', 'direct')}",
                severity="success",
                agent="leads",
            )
        except Exception:
            pass

        logger.info(f"Lead captured: {email} from {data.get('source_url', 'direct')}")

        # Return HTML redirect for form submissions, JSON for API calls
        if "application/json" in content_type:
            return {"success": True, "lead_id": lead_id}
        else:
            return HTMLResponse(
                content="""<html><body style="font-family:system-ui;text-align:center;padding:60px">
                <h2>Thank you!</h2>
                <p>We'll be in touch shortly.</p>
                <a href="https://successifier.com">Back to Successifier</a>
                </body></html>""",
                status_code=200
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lead capture failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to capture lead")


# ── Lead Pipeline (Dashboard API) ──────────────────────────────────────────

@router.get("/leads")
async def get_leads(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Get leads with optional status filter."""
    try:
        from shared.database import get_supabase
        sb = get_supabase()

        query = sb.table("leads").select("*").order("created_at", desc=True)
        if status:
            query = query.eq("status", status)
        query = query.range(offset, offset + limit - 1)

        result = query.execute()
        return {"success": True, "leads": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        logger.error(f"Failed to fetch leads: {e}")
        return {"success": False, "leads": [], "error": str(e)}


@router.get("/leads/stats")
async def get_lead_stats(request: Request):
    """Get lead pipeline statistics for dashboard."""
    try:
        from shared.database import get_supabase
        sb = get_supabase()
        tenant_id = getattr(request.state, "tenant_id", "default")

        query = sb.table("leads").select("status,score,utm_source,created_at")
        if tenant_id and tenant_id != "default":
            query = query.eq("tenant_id", tenant_id)
        all_leads = query.execute()
        leads = all_leads.data or []

        total = len(leads)
        by_status = {}
        by_source = {}
        for lead in leads:
            s = lead.get("status", "new")
            by_status[s] = by_status.get(s, 0) + 1
            src = lead.get("utm_source") or "direct"
            by_source[src] = by_source.get(src, 0) + 1

        qualified = sum(1 for l in leads if (l.get("score") or 0) >= 70)

        return {
            "success": True,
            "stats": {
                "total": total,
                "new": by_status.get("new", 0),
                "contacted": by_status.get("contacted", 0),
                "qualified": qualified,
                "meeting_booked": by_status.get("meeting_booked", 0),
                "converted": by_status.get("converted", 0),
                "by_source": by_source,
                "by_status": by_status,
            }
        }
    except Exception as e:
        logger.error(f"Failed to fetch lead stats: {e}")
        return {"success": False, "stats": {}, "error": str(e)}


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, updates: Dict[str, Any]):
    """Update a lead's status or data."""
    try:
        from shared.database import get_supabase
        sb = get_supabase()

        allowed_fields = {"status", "score", "name", "company", "phone", "notes"}
        filtered = {k: v for k, v in updates.items() if k in allowed_fields}
        filtered["updated_at"] = datetime.utcnow().isoformat()

        sb.table("leads").update(filtered).eq("id", lead_id).execute()
        return {"success": True, "lead_id": lead_id}
    except Exception as e:
        logger.error(f"Failed to update lead: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/leads/{lead_id}/touchpoints")
async def get_lead_touchpoints(lead_id: str):
    """Get all touchpoints for a lead."""
    try:
        from shared.database import get_supabase
        sb = get_supabase()

        result = sb.table("lead_touchpoints").select("*").eq("lead_id", lead_id).order("created_at", desc=True).execute()
        return {"success": True, "touchpoints": result.data or []}
    except Exception as e:
        return {"success": False, "touchpoints": [], "error": str(e)}
