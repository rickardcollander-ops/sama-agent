"""
Alerts API Routes
Manage alerts and approval workflows
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from shared.alerts import alert_system

router = APIRouter()
logger = logging.getLogger(__name__)


class ApprovalRequest(BaseModel):
    approved_by: str


class RejectionRequest(BaseModel):
    rejected_by: str
    reason: str


@router.get("/pending")
async def get_pending_approvals():
    """Get all alerts requiring approval"""
    try:
        approvals = await alert_system.get_pending_approvals()
        
        return {
            "success": True,
            "total": len(approvals),
            "approvals": approvals
        }
        
    except Exception as e:
        logger.error(f"Failed to get pending approvals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{alert_id}/approve")
async def approve_alert(alert_id: str, request: ApprovalRequest):
    """Approve a pending alert"""
    try:
        result = await alert_system.approve_alert(alert_id, request.approved_by)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to approve alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{alert_id}/reject")
async def reject_alert(alert_id: str, request: RejectionRequest):
    """Reject a pending alert"""
    try:
        result = await alert_system.reject_alert(
            alert_id,
            request.rejected_by,
            request.reason
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to reject alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))
