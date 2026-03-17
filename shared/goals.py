"""
Goal-Driven Behavior System
Tracks measurable goals and injects progress into agent OODA cycles.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from shared.database import get_supabase

logger = logging.getLogger(__name__)


class GoalTracker:
    """Manages persistent goals for agents."""

    def __init__(self):
        self._sb = None

    def _get_sb(self):
        if not self._sb:
            self._sb = get_supabase()
        return self._sb

    async def create_goal(
        self,
        goal_text: str,
        target_metric: str,
        target_value: float,
        baseline_value: float,
        deadline: str,
        owner_agent: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            sb = self._get_sb()
            result = sb.table("agent_goals").insert({
                "goal_text": goal_text,
                "target_metric": target_metric,
                "target_value": target_value,
                "baseline_value": baseline_value,
                "current_value": baseline_value,
                "deadline": deadline,
                "owner_agent": owner_agent,
                "status": "active",
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"[goals] Failed to create goal: {e}")
            return None

    async def get_active_goals(self, agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            sb = self._get_sb()
            query = sb.table("agent_goals").select("*").eq("status", "active")
            if agent_name:
                query = query.eq("owner_agent", agent_name)
            result = query.order("created_at", desc=True).execute()
            return result.data or []
        except Exception as e:
            logger.debug(f"[goals] Failed to fetch goals: {e}")
            return []

    async def update_progress(self, goal_id: str, current_value: float):
        try:
            sb = self._get_sb()
            sb.table("agent_goals").update({
                "current_value": current_value,
                "last_checked_at": datetime.utcnow().isoformat(),
            }).eq("id", goal_id).execute()
        except Exception as e:
            logger.error(f"[goals] Failed to update goal progress: {e}")

    async def check_goal_status(self, goal: Dict[str, Any]) -> str:
        """Evaluate progress: on_track, behind, achieved, failed."""
        target = goal.get("target_value", 0)
        current = goal.get("current_value", 0)
        baseline = goal.get("baseline_value", 0)
        deadline = goal.get("deadline", "")

        if not deadline or not target:
            return "unknown"

        try:
            deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return "unknown"

        now = datetime.utcnow()
        if current >= target:
            return "achieved"
        if now > deadline_dt:
            return "failed"

        # Calculate expected progress
        created = goal.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return "unknown"

        total_duration = (deadline_dt - created_dt).total_seconds()
        elapsed = (now - created_dt).total_seconds()
        if total_duration <= 0:
            return "unknown"

        time_pct = elapsed / total_duration
        progress_pct = (current - baseline) / (target - baseline) if (target - baseline) != 0 else 0

        return "on_track" if progress_pct >= time_pct * 0.8 else "behind"

    def format_goals_for_prompt(self, goals: List[Dict[str, Any]]) -> str:
        """Format active goals into a string for OODA prompt injection."""
        if not goals:
            return ""

        lines = ["## Active Goals\n"]
        for g in goals:
            target = g.get("target_value", 0)
            current = g.get("current_value", 0)
            baseline = g.get("baseline_value", 0)
            deadline = g.get("deadline", "unknown")[:10]
            progress = 0
            if target != baseline:
                progress = round((current - baseline) / (target - baseline) * 100, 1)

            lines.append(
                f"- {g.get('goal_text', 'Goal')}: "
                f"{current} / {target} ({progress}% done, deadline: {deadline})"
            )

        lines.append(
            "\nPrioritize actions that move these goals forward. "
            "If progress is behind schedule, suggest accelerated strategies."
        )
        return "\n".join(lines)

    async def get_prompt_context(self, agent_name: Optional[str] = None) -> str:
        """One-call: fetch goals and format for prompt injection."""
        goals = await self.get_active_goals(agent_name)
        return self.format_goals_for_prompt(goals)


# Global instance
goal_tracker = GoalTracker()
