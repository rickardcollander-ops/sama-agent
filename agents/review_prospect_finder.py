"""
Review-Based Prospect Finder
Identifies potential customers from competitor review data.
Analyzes competitor weaknesses to find companies likely to switch.
"""

import asyncio
import json
import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from anthropic import Anthropic
from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)


class ReviewProspectFinder:
    """
    Identifies potential customers based on competitor review intelligence.
    Uses AI to analyze dissatisfied competitor users and build prospect profiles.
    """

    SYSTEM_PROMPT = """You are a B2B SaaS sales intelligence analyst for Successifier, an AI-native Customer Success Platform.

Successifier's key advantages:
- AI-native: Built from the ground up with AI, not bolted on
- 40% churn reduction for customers
- 25% NRR improvement
- 85% less manual work through automation
- Affordable pricing vs enterprise competitors
- Fast time-to-value (weeks, not months)
- Modern UI/UX, not legacy enterprise software

Your job is to analyze competitor reviews and identify:
1. Companies/people who are unhappy with their current CS platform
2. Specific pain points that Successifier solves
3. Outreach messaging that would resonate with these prospects
4. Signals that indicate high switching intent"""

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = settings.CLAUDE_MODEL
        self.sb = None

    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb

    async def find_prospects_from_reviews(self, competitor_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Find potential prospects from competitor review data.

        Args:
            competitor_key: Optional - focus on a specific competitor. None = all competitors.
        """
        logger.info(f"Finding prospects from reviews (competitor: {competitor_key or 'all'})...")

        sb = self._get_sb()

        # Fetch latest competitor review analyses
        try:
            query = sb.table("competitor_reviews").select("*").order("monitored_at", desc=True)
            if competitor_key:
                query = query.eq("competitor_key", competitor_key)
            result = query.limit(20).execute()
            competitor_data = result.data or []
        except Exception as e:
            logger.warning(f"Could not fetch competitor reviews: {e}")
            competitor_data = []

        if not competitor_data:
            return {
                "success": False,
                "error": "No competitor review data available. Run competitor monitoring first."
            }

        # Analyze for prospect signals
        prospects = await self._analyze_prospect_signals(competitor_data)

        # Generate outreach templates
        outreach = await self._generate_outreach_templates(prospects)

        # Store results
        try:
            sb.table("review_prospects").insert({
                "prospects": json.dumps(prospects, default=str),
                "outreach_templates": json.dumps(outreach, default=str),
                "competitor_filter": competitor_key,
                "found_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.warning(f"Could not store prospects: {e}")

        return {
            "success": True,
            "total_prospects_identified": len(prospects.get("high_intent", [])) + len(prospects.get("medium_intent", [])),
            "prospects": prospects,
            "outreach_templates": outreach,
            "generated_at": datetime.utcnow().isoformat()
        }

    async def _analyze_prospect_signals(self, competitor_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Use AI to identify prospect signals from competitor reviews"""

        if not self.client:
            return self._fallback_prospect_signals(competitor_data)

        # Build context from competitor data
        summaries = []
        for entry in competitor_data[:10]:
            ai_analysis = entry.get("ai_analysis", "{}")
            if isinstance(ai_analysis, str):
                try:
                    ai_analysis = json.loads(ai_analysis)
                except json.JSONDecodeError:
                    ai_analysis = {}

            summaries.append({
                "competitor": entry.get("competitor"),
                "avg_rating": entry.get("avg_rating"),
                "weaknesses": ai_analysis.get("weaknesses", []),
                "switching_signals": ai_analysis.get("switching_signals", []),
                "pricing_sentiment": ai_analysis.get("pricing_sentiment", {}),
                "ideal_prospect_profile": ai_analysis.get("ideal_prospect_profile", {})
            })

        prompt = f"""Based on these competitor review analyses, identify prospect profiles for Successifier's sales team.

Competitor Intelligence:
{json.dumps(summaries, indent=2, default=str)}

Return JSON:
{{
    "high_intent": [
        {{
            "profile": "Description of the ideal prospect based on this intelligence",
            "source_competitor": "Which competitor they're likely leaving",
            "pain_points": ["specific pain points from reviews"],
            "trigger_events": ["events that indicate they're ready to switch"],
            "company_characteristics": {{
                "size": "SMB/Mid-Market/Enterprise",
                "industry": "...",
                "tech_stack_signals": ["..."]
            }},
            "outreach_angle": "How to approach this prospect",
            "urgency": "high|medium"
        }}
    ],
    "medium_intent": [
        {{
            "profile": "...",
            "source_competitor": "...",
            "pain_points": ["..."],
            "nurture_strategy": "How to nurture them until ready"
        }}
    ],
    "search_queries": {{
        "linkedin": ["search queries to find these prospects on LinkedIn"],
        "google": ["search queries to find these companies"],
        "job_boards": ["job posting signals that indicate they need a CS platform"]
    }},
    "icp_refinement": {{
        "best_fit_from_competitor": "Which competitor's users are best fit for Successifier",
        "reasoning": "...",
        "estimated_tam": "Rough estimate of addressable prospects"
    }}
}}"""

        try:
            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=3000,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}]
                )

            response = await asyncio.to_thread(_call)
            text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"AI prospect analysis failed: {e}")

        return self._fallback_prospect_signals(competitor_data)

    async def _generate_outreach_templates(self, prospects: Dict[str, Any]) -> Dict[str, Any]:
        """Generate outreach message templates based on prospect intelligence"""

        if not self.client:
            return {"templates": []}

        prompt = f"""Based on these prospect profiles, generate outreach templates for Successifier's sales team.

Prospects:
{json.dumps(prospects, indent=2, default=str)}

Generate 3-4 outreach templates targeting different prospect segments. Each should:
- Reference specific competitor pain points (without naming the competitor directly)
- Highlight how Successifier solves their exact problem
- Be personal, not generic
- Include a clear CTA

Return JSON:
{{
    "email_templates": [
        {{
            "name": "Template name",
            "target_segment": "Who this is for",
            "subject_line": "...",
            "body": "...",
            "follow_up": "Follow-up message 3 days later"
        }}
    ],
    "linkedin_messages": [
        {{
            "name": "Template name",
            "target_segment": "Who this is for",
            "connection_note": "Short connection request message",
            "follow_up_message": "Message after they accept"
        }}
    ],
    "ad_copy": [
        {{
            "target_segment": "...",
            "headline": "...",
            "description": "...",
            "cta": "..."
        }}
    ]
}}"""

        try:
            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=2500,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}]
                )

            response = await asyncio.to_thread(_call)
            text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"AI outreach generation failed: {e}")

        return {"templates": []}

    def _fallback_prospect_signals(self, competitor_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Basic prospect signal extraction without AI"""
        high_intent = []
        medium_intent = []

        for entry in competitor_data:
            competitor = entry.get("competitor", "Unknown")
            rating = entry.get("avg_rating", 5)

            if rating <= 3.5:
                high_intent.append({
                    "profile": f"Users dissatisfied with {competitor}",
                    "source_competitor": competitor,
                    "pain_points": ["Low satisfaction scores"],
                    "trigger_events": ["Contract renewal period"],
                    "outreach_angle": f"Offer a better alternative to {competitor}",
                    "urgency": "high"
                })
            elif rating <= 4.2:
                medium_intent.append({
                    "profile": f"Users with mixed feelings about {competitor}",
                    "source_competitor": competitor,
                    "pain_points": ["Looking for improvements"],
                    "nurture_strategy": f"Share comparison content vs {competitor}"
                })

        return {
            "high_intent": high_intent,
            "medium_intent": medium_intent,
            "search_queries": {
                "linkedin": [f"{c.get('competitor', '')} customer success manager" for c in competitor_data[:3]],
                "google": ["customer success platform alternative", "best customer success software"],
                "job_boards": ["customer success manager hiring"]
            },
            "icp_refinement": {}
        }

    async def get_latest_prospects(self) -> Dict[str, Any]:
        """Get the most recent prospect analysis"""
        try:
            sb = self._get_sb()
            result = sb.table("review_prospects").select("*").order("found_at", desc=True).limit(1).execute()
            if result.data:
                data = result.data[0]
                prospects = json.loads(data.get("prospects", "{}")) if isinstance(data.get("prospects"), str) else data.get("prospects", {})
                outreach = json.loads(data.get("outreach_templates", "{}")) if isinstance(data.get("outreach_templates"), str) else data.get("outreach_templates", {})
                return {
                    "success": True,
                    "prospects": prospects,
                    "outreach_templates": outreach,
                    "generated_at": data.get("found_at")
                }
            return {"success": False, "error": "No prospect data found. Run prospect finder first."}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Global instance
prospect_finder = ReviewProspectFinder()
