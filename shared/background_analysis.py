"""
Background analysis runner.

Provides a pattern for running long OODA analyses as background tasks
so the frontend can poll for progress instead of waiting.
"""

import asyncio
import logging
from typing import Callable, Awaitable, Dict, Any, Optional

from shared.database import get_supabase

logger = logging.getLogger(__name__)

# In-memory store of running analysis tasks (agent_name → asyncio.Task)
_running_tasks: Dict[str, asyncio.Task] = {}


async def start_background_analysis(
    agent_name: str,
    analysis_fn: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Start an OODA analysis as a background task.

    The analysis_fn (e.g. run_seo_analysis_with_ooda) creates its own
    OODALoop cycle internally. We don't create a duplicate — we just
    launch the task and let the frontend poll get_cycle_status() which
    finds the latest cycle for this agent.
    """
    # Prevent duplicate runs
    existing = _running_tasks.get(agent_name)
    if existing and not existing.done():
        return {
            "started": False,
            "status": "already_running",
            "message": f"{agent_name} analysis is already running. Poll /cycle-status for progress.",
        }

    async def _run():
        try:
            await analysis_fn()
        except Exception as e:
            logger.error(f"Background {agent_name} analysis failed: {e}")
            # The analysis_fn's own OODALoop handles fail_cycle internally
        finally:
            _running_tasks.pop(agent_name, None)

    task = asyncio.create_task(_run())
    _running_tasks[agent_name] = task

    return {
        "started": True,
        "status": "observing",
        "phase": "Starting analysis...",
        "progress": 5,
        "message": f"{agent_name} analysis started in background. Poll /cycle-status for progress.",
    }


async def get_cycle_status(agent_name: str, cycle_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get the status of the latest (or specific) OODA cycle for an agent.
    Returns phase, progress percentage, and whether it's done.
    """
    try:
        sb = get_supabase()

        if cycle_id:
            result = sb.table("agent_cycles").select("*").eq("id", cycle_id).execute()
        else:
            result = (
                sb.table("agent_cycles")
                .select("*")
                .eq("agent_name", agent_name)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        if not result.data:
            # No cycles yet — check if a background task is running
            if agent_name in _running_tasks and not _running_tasks[agent_name].done():
                return {"status": "observing", "phase": "Starting analysis...", "progress": 5, "done": False}
            return {"status": "idle", "phase": None, "progress": 0, "done": True}

        cycle = result.data[0]
        status = cycle.get("status", "unknown")

        # The OODA analyses end at "decide" phase (status becomes "acting")
        # but ACT happens later via /execute. For background polling purposes
        # we treat "acting" as done since the analysis part is complete.
        progress_map = {
            "observing": 15,
            "orienting": 40,
            "deciding": 65,
            "acting": 100,
            "reflecting": 100,
            "completed": 100,
            "failed": 100,
        }
        progress = progress_map.get(status, 0)
        done = status in ("acting", "reflecting", "completed", "failed")

        phase_labels = {
            "observing": "Collecting data...",
            "orienting": "Analyzing gaps, trends, and opportunities...",
            "deciding": "Generating strategy and prioritizing actions...",
            "acting": "Analysis complete — actions saved",
            "reflecting": "Analysis complete",
            "completed": "Analysis complete",
            "failed": f"Analysis failed: {cycle.get('error_message', 'unknown error')}",
        }

        return {
            "cycle_id": cycle.get("id"),
            "status": status,
            "phase": phase_labels.get(status, status),
            "progress": progress,
            "done": done,
            "error": cycle.get("error_message") if status == "failed" else None,
            "started_at": cycle.get("created_at"),
            "completed_at": cycle.get("completed_at"),
        }
    except Exception as e:
        logger.error(f"Error fetching cycle status for {agent_name}: {e}")
        # If the background task is still running, return a safe status
        if agent_name in _running_tasks and not _running_tasks[agent_name].done():
            return {"status": "observing", "phase": "Analysis in progress...", "progress": 20, "done": False}
        return {"status": "idle", "phase": None, "progress": 0, "done": True}
