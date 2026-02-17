"""
SAMA 2.0 Orchestrator Agent
Coordinates all specialist agents and manages cross-channel strategy
"""

import logging
from typing import Dict, Any, List
from anthropic import Anthropic

from shared.config import settings
from shared.event_bus import event_bus

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Central orchestrator that coordinates all SAMA agents
    Uses Claude Sonnet 4.5 for high-level decision making
    """
    
    SYSTEM_PROMPT = """You are SAMA — the Successifier Autonomous Marketing Agent. You are responsible for ALL inbound marketing activities for successifier.com, an AI-native Customer Success Platform for SaaS companies.

Your channels: SEO, Google Ads, Social Media (X/Twitter), Review Platforms, and Cross-Channel Analytics.

The LinkedIn Agent handles LinkedIn separately — coordinate with it but do not duplicate its work.

About Successifier:
AI-native Customer Success Platform that predicts churn, automates onboarding, and guides customers to success.
Key metrics: 40% churn reduction, 25% NRR improvement, 85% less manual work.
Plans from $79/month. 14-day free trial.

Decision authority:
- Execute autonomously: routine optimisations, content publishing, bid adjustments <20%, review responses, social posts
- Flag for human approval: budget increases >30%, new campaign launches, major landing page changes, negative review responses

Always cite data. Always state confidence level. Always provide next action."""
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"
    
    async def process_goal(self, goal: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Process a marketing goal and decompose into agent tasks
        
        Args:
            goal: High-level marketing objective
            context: Additional context (current metrics, constraints, etc.)
        
        Returns:
            Execution plan with tasks for each agent
        """
        logger.info(f"🎯 Processing goal: {goal}")
        
        # Build context for Claude
        context_str = self._build_context(context or {})
        
        # Get orchestration plan from Claude
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"""Goal: {goal}

Current Context:
{context_str}

Create an execution plan. Break this down into specific tasks for each agent:
- SEO Agent
- Content Agent
- Ads Agent
- Social Agent
- Review Agent
- Analytics Agent

For each task, specify:
1. Agent responsible
2. Action to take
3. Success criteria
4. Dependencies (if any)
5. Approval required (yes/no)

Format as JSON."""
            }]
        )
        
        # Parse response and distribute tasks
        plan = self._parse_plan(response.content[0].text)
        
        logger.info(f"✅ Generated plan with {len(plan.get('tasks', []))} tasks")
        
        return plan
    
    async def coordinate_with_linkedin_agent(self, event_type: str, data: Dict[str, Any]):
        """
        Send coordination event to LinkedIn Agent
        
        Args:
            event_type: Type of coordination event
            data: Event data
        """
        if settings.LINKEDIN_AGENT_EVENT_BUS_ENABLED:
            await event_bus.publish(
                event_type=event_type,
                target_agent="linkedin_agent",
                data=data
            )
            logger.info(f"📤 Sent {event_type} to LinkedIn Agent")
    
    def _build_context(self, context: Dict[str, Any]) -> str:
        """Build context string for Claude"""
        parts = []
        
        if "current_metrics" in context:
            parts.append(f"Current Metrics:\n{context['current_metrics']}")
        
        if "constraints" in context:
            parts.append(f"Constraints:\n{context['constraints']}")
        
        if "recent_events" in context:
            parts.append(f"Recent Events:\n{context['recent_events']}")
        
        return "\n\n".join(parts) if parts else "No additional context provided."
    
    def _parse_plan(self, response_text: str) -> Dict[str, Any]:
        """Parse Claude's response into structured plan"""
        # TODO: Implement robust JSON parsing with fallback
        # For now, return basic structure
        return {
            "raw_response": response_text,
            "tasks": [],
            "requires_approval": False
        }


# Global orchestrator instance
orchestrator = OrchestratorAgent()
