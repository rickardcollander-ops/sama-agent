"""
Agent Learning & Memory System
Retrieves past learnings and injects them into OODA DECIDE prompts.
Enables agents to get smarter over time.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from shared.database import get_supabase

logger = logging.getLogger(__name__)


class AgentMemory:
    """Manages agent learnings: retrieval, formatting, and decay."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._sb = None

    def _get_sb(self):
        if not self._sb:
            self._sb = get_supabase()
        return self._sb

    async def get_relevant_learnings(
        self,
        context: Optional[str] = None,
        limit: int = 15,
        min_confidence: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the most relevant and recent learnings for this agent.
        Filters by confidence and recency.
        """
        try:
            sb = self._get_sb()
            query = sb.table("agent_learnings") \
                .select("*") \
                .eq("agent_name", self.agent_name) \
                .gte("confidence_score", min_confidence) \
                .order("created_at", desc=True) \
                .limit(limit)

            result = query.execute()
            return result.data or []
        except Exception as e:
            logger.debug(f"[memory] Failed to load learnings for {self.agent_name}: {e}")
            return []

    def format_learnings_for_prompt(self, learnings: List[Dict[str, Any]]) -> str:
        """
        Format learnings into a string suitable for injection into Claude prompts.
        """
        if not learnings:
            return ""

        lines = ["## Past Learnings (what worked and what didn't)\n"]
        for l in learnings:
            confidence = l.get("confidence_score", 0.5)
            stars = "high" if confidence >= 0.7 else "medium" if confidence >= 0.4 else "low"
            action = l.get("action_taken", "Unknown action")
            outcome = l.get("actual_outcome", l.get("expected_outcome", ""))
            learning_type = l.get("learning_type", "insight")

            line = f"- [{learning_type}] {action}"
            if outcome:
                line += f" → {outcome}"
            line += f" (confidence: {stars})"
            lines.append(line)

        lines.append(
            "\nUse these learnings to inform your decisions. "
            "Prioritize strategies that have worked before and avoid repeating failures."
        )
        return "\n".join(lines)

    async def get_prompt_context(self, context: Optional[str] = None) -> str:
        """One-call method: fetch learnings and format for prompt injection."""
        learnings = await self.get_relevant_learnings(context=context)
        return self.format_learnings_for_prompt(learnings)

    async def store_reflection(
        self,
        cycle_id: str,
        action_taken: str,
        expected_outcome: str,
        actual_outcome: str,
        confidence: float = 0.5,
        learning_type: str = "outcome",
        context: Optional[Dict[str, Any]] = None,
    ):
        """Store a learning from a completed action's reflection."""
        try:
            sb = self._get_sb()
            sb.table("agent_learnings").insert({
                "agent_name": self.agent_name,
                "cycle_id": cycle_id,
                "learning_type": learning_type,
                "action_taken": action_taken,
                "expected_outcome": expected_outcome,
                "actual_outcome": actual_outcome,
                "confidence_score": confidence,
                "context": context or {},
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            logger.info(f"[memory] Stored learning for {self.agent_name}: {action_taken[:60]}")
        except Exception as e:
            logger.error(f"[memory] Failed to store learning: {e}")

    async def run_reflection_for_completed_actions(self):
        """
        Find actions completed >7 days ago without reflection,
        compare expected vs actual outcomes, store learnings.
        """
        try:
            sb = self._get_sb()
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

            result = sb.table("agent_actions") \
                .select("id, action_id, title, expected_outcome, execution_result") \
                .eq("agent_name", self.agent_name) \
                .eq("status", "completed") \
                .lte("executed_at", cutoff) \
                .is_("reflected_at", "null") \
                .limit(10) \
                .execute()

            actions = result.data or []
            if not actions:
                return 0

            reflected = 0
            for action in actions:
                expected = action.get("expected_outcome", "")
                actual_result = action.get("execution_result", {})
                if not expected:
                    continue

                actual_str = ""
                if isinstance(actual_result, dict):
                    actual_str = actual_result.get("summary", str(actual_result)[:200])
                else:
                    actual_str = str(actual_result)[:200]

                # Determine confidence based on whether outcome was achieved
                confidence = 0.6 if actual_result else 0.3

                await self.store_reflection(
                    cycle_id="reflection-batch",
                    action_taken=action.get("title", ""),
                    expected_outcome=expected,
                    actual_outcome=actual_str or "No result data available",
                    confidence=confidence,
                )

                # Mark as reflected
                try:
                    sb.table("agent_actions").update({
                        "reflected_at": datetime.utcnow().isoformat()
                    }).eq("id", action["id"]).execute()
                except Exception:
                    pass

                reflected += 1

            logger.info(f"[memory] Reflected on {reflected} completed actions for {self.agent_name}")
            return reflected

        except Exception as e:
            logger.error(f"[memory] Reflection batch failed for {self.agent_name}: {e}")
            return 0
