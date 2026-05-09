"""
AI Readability API routes.

Reads from the ``site_audits`` table (already populated by SiteAuditAgent
when it runs). This route exists so the dashboard can ask for the latest
AI-readability snapshot without dragging the full audit payload — the
detail-level chunk data is huge and only needed on the audit detail page.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/status")
async def get_status() -> Dict[str, str]:
    return {"agent": "ai_readability", "status": "operational"}


@router.get("/summary")
async def get_summary(request: Request, days: int = 30) -> Dict[str, Any]:
    """Latest AI-readability snapshot for the calling tenant.

    Returns a compact subset of the audit payload — enough for the GEO page
    scorecard and the insights overview tile, without the per-chunk detail.
    The detail-level data (chunks per page) is on the audit detail page,
    which already loads the full audit payload.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    days = max(1, min(days, 365))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        sb = get_supabase()
        # Latest completed audit with a non-null AI-readability score.
        latest_q = (
            sb.table("site_audits")
            .select(
                "id, ai_readability_score, ai_readability_run_at, payload"
            )
            .eq("tenant_id", tenant_id)
            .eq("status", "completed")
            .not_.is_("ai_readability_score", "null")
            .order("ai_readability_run_at", desc=True)
            .limit(1)
            .execute()
        )
        latest_rows = latest_q.data or []
        if not latest_rows:
            return _empty()
        latest = latest_rows[0]

        # History — up to 30 prior runs in window for sparkline.
        history_q = (
            sb.table("site_audits")
            .select("ai_readability_score, ai_readability_run_at")
            .eq("tenant_id", tenant_id)
            .eq("status", "completed")
            .not_.is_("ai_readability_score", "null")
            .gte("ai_readability_run_at", cutoff)
            .order("ai_readability_run_at", desc=False)
            .limit(30)
            .execute()
        )
        history_rows = history_q.data or []

        payload = latest.get("payload") or {}
        ar = payload.get("ai_readability") or {}
        return {
            "audit_id": latest.get("id"),
            "overall_score": latest.get("ai_readability_score"),
            "sub_scores": ar.get("sub_scores") or {},
            "action_points": ar.get("action_points") or [],
            "page_count": len(ar.get("page_analyses") or []),
            "last_run_at": latest.get("ai_readability_run_at"),
            "history": [
                {
                    "date": r.get("ai_readability_run_at"),
                    "score": r.get("ai_readability_score"),
                }
                for r in history_rows
                if r.get("ai_readability_run_at") is not None
            ],
        }
    except Exception as e:
        logger.error("ai_readability summary error: %s", e)
        return _empty()


def _empty() -> Dict[str, Any]:
    return {
        "audit_id": None,
        "overall_score": None,
        "sub_scores": {},
        "action_points": [],
        "page_count": 0,
        "last_run_at": None,
        "history": [],
    }


@router.get("/detail/{audit_id}")
async def get_detail(audit_id: str, request: Request) -> Dict[str, Any]:
    """Full AI-readability block for one audit — used by the audit detail
    page when the user opens the ``ai-readability`` tab. Tenant-scoped to
    prevent cross-tenant data exposure."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        row_q = (
            sb.table("site_audits")
            .select("id, payload")
            .eq("tenant_id", tenant_id)
            .eq("id", audit_id)
            .limit(1)
            .execute()
        )
        rows = row_q.data or []
        if not rows:
            return {"error": "not_found"}
        payload = rows[0].get("payload") or {}
        return payload.get("ai_readability") or {}
    except Exception as e:
        logger.error("ai_readability detail error: %s", e)
        return {"error": str(e)}
