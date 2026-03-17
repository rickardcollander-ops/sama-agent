"""
Notifications API — in-app notifications for the dashboard
"""

from fastapi import APIRouter
from typing import Optional
import logging

from shared.notifications import notification_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/notifications")
async def list_notifications(limit: int = 20, unread_only: bool = True):
    """Get notifications for the dashboard."""
    if unread_only:
        notifications = await notification_service.get_unread(limit=limit)
    else:
        try:
            from shared.database import get_supabase
            sb = get_supabase()
            result = sb.table("notifications") \
                .select("*") \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            notifications = result.data or []
        except Exception:
            notifications = []

    return {"notifications": notifications, "total": len(notifications)}


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: str):
    """Mark a single notification as read."""
    await notification_service.mark_read(notification_id)
    return {"success": True}


@router.post("/notifications/read-all")
async def mark_all_read():
    """Mark all notifications as read."""
    await notification_service.mark_all_read()
    return {"success": True}
