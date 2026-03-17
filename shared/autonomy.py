"""
Autonomous Decision Escalation Framework
Classifies agent actions into tiers determining auto-execute vs human approval.
"""

import logging
from typing import Dict, Any, Optional
from enum import Enum

from shared.config import settings

logger = logging.getLogger(__name__)


class DecisionTier(str, Enum):
    AUTO_EXECUTE = "auto_execute"            # Tier 1: execute immediately, log only
    AUTO_EXECUTE_NOTIFY = "auto_execute_notify"  # Tier 2: execute + send notification
    REQUIRE_APPROVAL = "require_approval"     # Tier 3: queue for human review


# ── Policy Rules ─────────────────────────────────────────────────────────────

# Default tier per action type — can be overridden via Supabase agent_policies
DEFAULT_POLICIES: Dict[str, DecisionTier] = {
    # Auto-execute (low risk, routine)
    "keyword_tracking": DecisionTier.AUTO_EXECUTE,
    "metrics_collection": DecisionTier.AUTO_EXECUTE,
    "review_response_positive": DecisionTier.AUTO_EXECUTE,
    "social_repost": DecisionTier.AUTO_EXECUTE,
    "monitoring": DecisionTier.AUTO_EXECUTE,

    # Auto-execute + notify (medium risk)
    "content_creation": DecisionTier.AUTO_EXECUTE_NOTIFY,
    "content_refresh": DecisionTier.AUTO_EXECUTE_NOTIFY,
    "social_post": DecisionTier.AUTO_EXECUTE_NOTIFY,
    "bid_adjustment_small": DecisionTier.AUTO_EXECUTE_NOTIFY,
    "seo_technical_fix": DecisionTier.AUTO_EXECUTE_NOTIFY,

    # Require approval (high risk)
    "content_publish": DecisionTier.REQUIRE_APPROVAL,
    "review_response_negative": DecisionTier.REQUIRE_APPROVAL,
    "budget_increase": DecisionTier.REQUIRE_APPROVAL,
    "campaign_creation": DecisionTier.REQUIRE_APPROVAL,
    "campaign_pause": DecisionTier.REQUIRE_APPROVAL,
    "landing_page_change": DecisionTier.REQUIRE_APPROVAL,
    "strategy_change": DecisionTier.REQUIRE_APPROVAL,
    "investigation": DecisionTier.REQUIRE_APPROVAL,
}


class AutonomyFramework:
    """Classifies actions and determines execution tier."""

    def __init__(self):
        self._custom_policies: Dict[str, DecisionTier] = {}

    async def load_policies_from_db(self):
        """Optionally load custom policies from Supabase agent_policies table."""
        try:
            from shared.database import get_supabase
            sb = get_supabase()
            result = sb.table("agent_policies") \
                .select("action_type, tier") \
                .execute()
            for row in (result.data or []):
                try:
                    self._custom_policies[row["action_type"]] = DecisionTier(row["tier"])
                except (ValueError, KeyError):
                    pass
            if self._custom_policies:
                logger.info(f"[autonomy] Loaded {len(self._custom_policies)} custom policies from DB")
        except Exception as e:
            logger.debug(f"[autonomy] No custom policies loaded: {e}")

    def classify(self, action: Dict[str, Any]) -> DecisionTier:
        """
        Determine the execution tier for an action.

        Considers:
        - action_type mapping
        - financial impact thresholds
        - config auto-publish flags
        """
        action_type = action.get("type", action.get("action_type", ""))
        priority = action.get("priority", "medium")

        # 1. Check custom DB policies first
        if action_type in self._custom_policies:
            return self._custom_policies[action_type]

        # 2. Check default policies
        if action_type in DEFAULT_POLICIES:
            tier = DEFAULT_POLICIES[action_type]
        else:
            # Unknown type: default based on priority
            if priority == "critical" or priority == "high":
                tier = DecisionTier.REQUIRE_APPROVAL
            else:
                tier = DecisionTier.AUTO_EXECUTE_NOTIFY

        # 3. Override based on config flags
        tier = self._apply_config_overrides(action_type, tier)

        # 4. Financial impact escalation
        tier = self._check_financial_impact(action, tier)

        return tier

    def _apply_config_overrides(self, action_type: str, tier: DecisionTier) -> DecisionTier:
        """Apply human-in-the-loop config overrides."""
        if action_type in ("content_creation", "content_publish"):
            if not settings.AUTO_PUBLISH_BLOG_POSTS:
                return DecisionTier.REQUIRE_APPROVAL

        if action_type == "social_post":
            if not settings.AUTO_PUBLISH_SOCIAL_POSTS:
                return DecisionTier.REQUIRE_APPROVAL

        if action_type == "review_response_positive":
            if not settings.AUTO_RESPOND_REVIEWS_POSITIVE:
                return DecisionTier.REQUIRE_APPROVAL

        if action_type == "review_response_negative":
            if not settings.AUTO_RESPOND_REVIEWS_NEGATIVE:
                return DecisionTier.REQUIRE_APPROVAL

        return tier

    def _check_financial_impact(self, action: Dict[str, Any], tier: DecisionTier) -> DecisionTier:
        """Escalate if financial impact exceeds threshold."""
        budget_change = action.get("budget_change_pct", 0)

        if abs(budget_change) > settings.BUDGET_CHANGE_APPROVAL_THRESHOLD:
            return DecisionTier.REQUIRE_APPROVAL
        elif abs(budget_change) > 0.10:
            return max(tier, DecisionTier.AUTO_EXECUTE_NOTIFY, key=lambda t: list(DecisionTier).index(t))

        return tier

    def should_auto_execute(self, action: Dict[str, Any]) -> bool:
        """Convenience: returns True if action can be auto-executed."""
        tier = self.classify(action)
        return tier in (DecisionTier.AUTO_EXECUTE, DecisionTier.AUTO_EXECUTE_NOTIFY)

    def should_notify(self, action: Dict[str, Any]) -> bool:
        """Convenience: returns True if action should trigger a notification."""
        tier = self.classify(action)
        return tier in (DecisionTier.AUTO_EXECUTE_NOTIFY, DecisionTier.REQUIRE_APPROVAL)


# Global instance
autonomy = AutonomyFramework()
