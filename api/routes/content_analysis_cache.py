"""
Persistent analysis cache for the content agent (and other agents that
want to reuse the same table).

The frontend used to lose its analysis the moment the in-memory cycle
status was cleared on the agent. With this route group:

* ``POST /api/content/analysis/save``    — frontend pushes the completed
  snapshot (summary + actions). The server writes a single row into
  ``content_analysis_cache`` (upsert per tenant+agent) and appends to
  ``content_analysis_history``.
* ``GET  /api/content/analysis/latest``  — load the cached snapshot on page
  mount so the user immediately sees the last analysis.
* ``GET  /api/content/analysis/history`` — list past analyses (most recent
  first).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class AnalysisSavePayload(BaseModel):
    agent: str = "content"
    cycle_id: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    payload: Optional[Dict[str, Any]] = None  # full snapshot if caller wants


@router.post("/analysis/save")
async def save_analysis(request: Request, body: AnalysisSavePayload):
    """Cache + append the latest analysis snapshot."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    agent = body.agent or "content"
    snapshot: Dict[str, Any] = body.payload or {}
    if body.summary is not None:
        snapshot["summary"] = body.summary
    if body.actions is not None:
        snapshot["actions"] = body.actions
    snapshot.setdefault("saved_at", datetime.now(timezone.utc).isoformat())

    if not snapshot:
        return {"success": False, "error": "Empty payload"}

    try:
        sb = get_supabase()
        # Upsert into the cache (one row per tenant+agent).
        sb.table("content_analysis_cache").upsert(
            {
                "tenant_id": tenant_id,
                "agent": agent,
                "payload": snapshot,
                "cycle_id": body.cycle_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="tenant_id,agent",
        ).execute()

        # Append to history.
        try:
            sb.table("content_analysis_history").insert(
                {
                    "tenant_id": tenant_id,
                    "agent": agent,
                    "cycle_id": body.cycle_id,
                    "payload": snapshot,
                    "summary": body.summary or {},
                }
            ).execute()
        except Exception as e:
            logger.debug(f"Failed to append analysis history: {e}")

        return {"success": True}
    except Exception as e:
        logger.error(f"save_analysis error: {e}")
        return {"success": False, "error": str(e)}


@router.get("/analysis/latest")
async def get_latest_analysis(request: Request, agent: str = "content"):
    """Return the cached latest analysis for the calling tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("content_analysis_cache")
            .select("payload,cycle_id,created_at")
            .eq("tenant_id", tenant_id)
            .eq("agent", agent)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return {"analysis": None}
        row = rows[0]
        return {
            "analysis": row.get("payload") or {},
            "cycle_id": row.get("cycle_id"),
            "cached_at": row.get("created_at"),
        }
    except Exception as e:
        logger.error(f"get_latest_analysis error: {e}")
        return {"analysis": None, "error": str(e)}


@router.get("/analysis/history")
async def list_analysis_history(request: Request, agent: str = "content", limit: int = 20):
    """List past analyses for the calling tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("content_analysis_history")
            .select("id,cycle_id,summary,created_at")
            .eq("tenant_id", tenant_id)
            .eq("agent", agent)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"history": result.data or []}
    except Exception as e:
        logger.error(f"list_analysis_history error: {e}")
        return {"history": [], "error": str(e)}


@router.get("/analysis/history/{history_id}")
async def get_analysis_history_entry(history_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        result = (
            sb.table("content_analysis_history")
            .select("*")
            .eq("id", history_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return {"entry": None}
        return {"entry": rows[0]}
    except Exception as e:
        logger.error(f"get_analysis_history_entry error: {e}")
        return {"entry": None, "error": str(e)}
