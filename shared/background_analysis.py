"""
Background analysis runner.

Provides a pattern for running long OODA analyses as background tasks
so the frontend can poll for progress instead of waiting.
"""

import asyncio
import logging
from typing import Callable, Awaitable, Dict, Any, Optional

from shared.database import get_supabase
from shared.ooda_loop import OODALoop

logger = logging.getLogger(__name__)

# In-memory store of running analysis tasks (cycle_id → asyncio.Task)
_running_tasks: Dict[str, asyncio.Task] = {}


async def start_background_analysis(
    agent_name: str,
    analysis_fn: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Start an OODA analysis as a background task.

    1. Creates an OODA cycle record (status=observing) in agent_cycles
    2. Launches the analysis_fn as a fire-and-forget asyncio task
    3. Returns immediately with the cycle_id for polling

    The analysis_fn is the existing run_*_analysis_with_ooda() function.
    It already updates agent_cycles status as it progresses.
    """
    # Create cycle record so frontend can start polling immediately
    ooda = OODALoop(agent_name=agent_name)
    cycle_id = await ooda.start_cycle()

    async def _run():
        try:
            # Run the actual analysis (it manages its own OODA cycle internally,
            # but we already started one — the analysis will create a second one.
            # That's fine, the frontend polls for the latest active cycle.)
            await analysis_fn()
        except Exception as e:
            logger.error(f"Background {agent_name} analysis failed: {e}")
            # Mark cycle as failed
            try:
                await ooda.fail_cycle(str(e))
            except Exception:
                pass
        finally:
            _running_tasks.pop(cycle_id, None)

    task = asyncio.create_task(_run())
    _running_tasks[cycle_id] = task

    return {
        "started": True,
        "cycle_id": cycle_id,
        "status": "observing",
        "message": f"{agent_name} analysis started in background. Poll /cycle-status for progress.",
    }


async def get_cycle_status(agent_name: str, cycle_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get the status of the latest (or specific) OODA cycle for an agent.
    Returns phase, progress percentage, and whether it's done.
    """
    sb = get_supabase()

    if cycle_id:
        result = sb.table("agent_cycles").select("*").eq("id", cycle_id).execute()
    else:
        # Get latest cycle for this agent
        result = (
            sb.table("agent_cycles")
            .select("*")
            .eq("agent_name", agent_name)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

    if not result.data:
        return {"status": "idle", "phase": None, "progress": 0, "done": True}

    cycle = result.data[0]
    status = cycle.get("status", "unknown")

    # Map status to progress percentage
    progress_map = {
        "observing": 15,
        "orienting": 40,
        "deciding": 65,
        "acting": 80,
        "reflecting": 90,
        "completed": 100,
        "failed": 100,
    }
    progress = progress_map.get(status, 0)
    done = status in ("completed", "failed")

    # Phase labels for UI
    phase_labels = {
        "observing": "Collecting data from GSC, keywords, and technical checks...",
        "orienting": "Analyzing gaps, trends, and opportunities...",
        "deciding": "Generating strategy and prioritizing actions...",
        "acting": "Saving actions to queue...",
        "reflecting": "Recording learnings...",
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
