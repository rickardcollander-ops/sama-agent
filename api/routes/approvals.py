"""
Approval queue API routes.

Tenants who opt out of auto-publish (tenant_config.auto_publish_blog_posts /
auto_publish_social_posts) get drafts written here instead. The /c/approvals
UI lets a human review, edit, approve, or reject before publication.

Endpoints (all tenant-scoped):
  GET   /api/approvals             list pending approvals (default)
  GET   /api/approvals?status=...  list by status
  POST  /api/approvals/{id}/approve  mark approved (and trigger publish)
  POST  /api/approvals/{id}/reject   mark rejected
  PATCH /api/approvals/{id}        edit body/title before approving

Note: actual publishing on approval is left as a TODO — the agents that
write rows here also own publication. We mark the row 'approved' and the
content/social agents pick it up on next cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.database import get_supabase, run_db

router = APIRouter()
logger = logging.getLogger(__name__)


class ApprovalEdit(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ApprovalDecision(BaseModel):
    note: Optional[str] = None


@router.get("")
async def list_approvals(request: Request, status: str = "pending", limit: int = 50):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = await run_db(lambda: (
            sb.table("pending_approvals")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("status", status)
            .order("created_at", desc=True)
            .limit(min(limit, 100))
            .execute()
        ))
        return {"approvals": result.data or []}
    except Exception as e:
        logger.error(f"list_approvals failed: {e}")
        return {"approvals": []}


@router.get("/{approval_id}")
async def get_approval(approval_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = await run_db(lambda: (
            sb.table("pending_approvals")
            .select("*")
            .eq("id", approval_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        ))
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Approval not found")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_approval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{approval_id}")
async def edit_approval(approval_id: str, payload: ApprovalEdit, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    update: Dict[str, Any] = {}
    if payload.title is not None:
        update["title"] = payload.title
    if payload.body is not None:
        update["body"] = payload.body
    if payload.metadata is not None:
        update["metadata"] = payload.metadata
    if not update:
        raise HTTPException(status_code=400, detail="nothing to update")
    sb = get_supabase()
    try:
        await run_db(lambda: sb.table("pending_approvals").update(update).eq("id", approval_id).eq("tenant_id", tenant_id).execute())
        return {"ok": True}
    except Exception as e:
        logger.error(f"edit_approval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{approval_id}/approve")
async def approve(approval_id: str, payload: ApprovalDecision, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        # Load the row first so we can hand the linked content piece off to the
        # publish bridge once it's approved.
        existing = await run_db(lambda: (
            sb.table("pending_approvals")
            .select("kind, metadata")
            .eq("id", approval_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        ))
        row = (existing.data or [{}])[0]

        update = {
            "status": "approved",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewer_note": payload.note,
        }
        await run_db(lambda: sb.table("pending_approvals").update(update).eq("id", approval_id).eq("tenant_id", tenant_id).execute())

        # Hand the approved content piece to the frontend publish bridge: flip
        # the piece to 'approved' and pin its plan item to publish now. The
        # dashboard's 5-min publish cron ingests pieces whose status is
        # 'approved' with a due scheduled_for and ships them to the tenant's own
        # destination (CMS or GitHub) — the single publisher for both modes.
        # Best-effort: a hiccup here must not fail the approval itself.
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        piece_id = (meta or {}).get("piece_id")
        if row.get("kind") == "content" and piece_id:
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                await run_db(lambda: sb.table("content_pieces").update(
                    {"status": "approved"}
                ).eq("id", piece_id).eq("tenant_id", tenant_id).execute())
                await run_db(lambda: sb.table("content_plan_items").update(
                    {"scheduled_for": now_iso, "auto_publish_on_schedule": True}
                ).eq("content_piece_id", piece_id).eq("tenant_id", tenant_id).execute())
            except Exception as e:
                logger.warning(f"approve: publish handoff failed for piece {piece_id}: {e}")

        return {"ok": True, "status": "approved"}
    except Exception as e:
        logger.error(f"approve failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{approval_id}/reject")
async def reject(approval_id: str, payload: ApprovalDecision, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        update = {
            "status": "rejected",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewer_note": payload.note,
        }
        await run_db(lambda: sb.table("pending_approvals").update(update).eq("id", approval_id).eq("tenant_id", tenant_id).execute())
        return {"ok": True, "status": "rejected"}
    except Exception as e:
        logger.error(f"reject failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Helper used by content/social agents ─────────────────────────────────────

def queue_for_approval(
    tenant_id: str,
    kind: str,
    channel: str,
    title: str,
    body: str,
    metadata: Optional[Dict[str, Any]] = None,
    agent_name: Optional[str] = None,
    created_by_agent_run: Optional[str] = None,
) -> Optional[str]:
    """
    Insert a draft into pending_approvals. Returns the new row id or None on
    failure. Called by the content/social/reviews agents instead of publishing
    directly when the tenant has auto_publish_X = false.
    """
    sb = get_supabase()
    try:
        result = sb.table("pending_approvals").insert({
            "tenant_id": tenant_id,
            "kind": kind,
            "channel": channel,
            "agent_name": agent_name,
            "title": title,
            "body": body,
            "metadata": metadata or {},
            "status": "pending",
            "created_by_agent_run": created_by_agent_run,
        }).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        logger.warning(f"queue_for_approval failed: {e}")
    return None
