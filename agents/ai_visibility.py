"""
AI Visibility Agent - Generative Engine Optimization (GEO)
Monitors and optimizes how AI assistants (ChatGPT, Claude, Perplexity, Gemini)
recommend Successifier when users ask customer success questions.

Unlike SEO (ranking in Google), GEO focuses on getting mentioned in AI-generated answers.
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase
from .models import (
    AI_VISIBILITY_CHECKS_TABLE,
    AI_CITATIONS_TABLE,
    AI_VISIBILITY_GAPS_TABLE,
)

logger = logging.getLogger(__name__)


# Prompts that potential Successifier customers ask AI assistants
# Organized by category for structured analysis
MONITORING_PROMPTS = {
    "tool_recommendation": [
        "What is the best customer success platform for a SaaS company with 1000 customers?",
        "What software should I use to manage customer success at scale?",
        "Best AI-powered customer success tools in 2024",
        "What tools do customer success teams use to track customer health?",
    ],
    "competitor_alternative": [
        "What are the best alternatives to Gainsight?",
        "ChurnZero alternatives that are more affordable",
        "Totango competitors for mid-market SaaS",
        "Cheaper alternatives to Gainsight for small CS teams",
    ],
    "use_case": [
        "How do I reduce customer churn in my SaaS product?",
        "How do I build a customer health score?",
        "What's the best way to automate customer success workflows?",
        "How can I improve my Net Revenue Retention (NRR)?",
        "How do I scale my customer success team without hiring?",
    ],
    "buying_intent": [
        "Customer success software with AI features",
        "Customer success platform pricing comparison",
        "Which customer success tool has the best ROI?",
        "Customer success software for SaaS startups",
    ],
}

# Competitors to specifically track in AI responses
TRACKED_COMPETITORS = [
    "gainsight",
    "churnzero",
    "totango",
    "planhat",
    "clientsuccess",
    "vitally",
    "catalyst",
]


class AIVisibilityAgent:
    """
    AI Visibility Agent responsible for:
    - Monitoring AI assistant responses for Successifier mentions
    - Identifying gaps where competitors are mentioned instead
    - Generating content and citation recommendations for better AI visibility
    - Tracking visibility trends over time
    """

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-opus-4-6"
        self.sb = None

    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb

    async def run_visibility_check(self) -> Dict[str, Any]:
        """
        Run a full AI visibility monitoring cycle.
        Queries each monitoring prompt and records results.
        """
        logger.info("🤖 Starting AI visibility check...")

        results = {
            "checked_at": datetime.utcnow().isoformat(),
            "total_prompts": 0,
            "successifier_mentioned": 0,
            "mention_rate": 0.0,
            "avg_mention_rank": None,
            "top_competing_tools": {},
            "gaps_identified": [],
            "checks": [],
        }

        all_ranks = []
        competitor_counts: Dict[str, int] = {}

        for category, prompts in MONITORING_PROMPTS.items():
            for prompt in prompts:
                try:
                    check = await self._check_single_prompt(prompt, category)
                    results["checks"].append(check)
                    results["total_prompts"] += 1

                    if check["successifier_mentioned"]:
                        results["successifier_mentioned"] += 1
                        if check["mention_rank"]:
                            all_ranks.append(check["mention_rank"])

                    # Tally competitor mentions
                    for comp in check.get("competitors_mentioned", []):
                        name = comp["name"]
                        competitor_counts[name] = competitor_counts.get(name, 0) + 1

                    # Identify gaps
                    if not check["successifier_mentioned"]:
                        gap = self._identify_gap(check)
                        if gap:
                            results["gaps_identified"].append(gap)

                    # Persist to DB
                    await self._save_check(check)

                except Exception as e:
                    logger.error(f"❌ Error checking prompt '{prompt[:50]}...': {e}")

        # Compute summary stats
        if results["total_prompts"] > 0:
            results["mention_rate"] = round(
                results["successifier_mentioned"] / results["total_prompts"], 2
            )
        if all_ranks:
            results["avg_mention_rank"] = round(sum(all_ranks) / len(all_ranks), 1)

        results["top_competing_tools"] = dict(
            sorted(competitor_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        )

        # Persist gaps
        for gap in results["gaps_identified"]:
            await self._save_gap(gap)

        logger.info(
            f"✅ Visibility check complete. "
            f"Mentioned in {results['successifier_mentioned']}/{results['total_prompts']} prompts "
            f"({results['mention_rate']*100:.0f}%)"
        )

        return results

    async def _check_single_prompt(self, prompt: str, category: str) -> Dict[str, Any]:
        """
        Ask Claude to answer a prompt as an AI assistant would,
        then analyze the response for Successifier mentions.
        This is a proxy for what AI assistants (Perplexity, ChatGPT) know.
        """
        if not self.client:
            return self._empty_check(prompt, category)

        # Step 1: Generate AI response to the prompt
        ai_response = await self._simulate_ai_response(prompt)

        # Step 2: Analyze the response
        analysis = await self._analyze_response(prompt, ai_response)

        return {
            "prompt": prompt,
            "prompt_category": category,
            "checked_at": datetime.utcnow().isoformat(),
            "ai_response": ai_response,
            "successifier_mentioned": analysis["successifier_mentioned"],
            "mention_rank": analysis.get("mention_rank"),
            "mention_context": analysis.get("mention_context"),
            "mention_sentiment": analysis.get("mention_sentiment"),
            "competitors_mentioned": analysis.get("competitors_mentioned", []),
            "sources_cited": analysis.get("sources_cited", []),
            "check_source": "claude_proxy",
        }

    async def _simulate_ai_response(self, prompt: str) -> str:
        """
        Use Claude to generate the kind of answer an AI assistant would give,
        based on its training knowledge about customer success tools.
        """
        system = (
            "You are an AI assistant with comprehensive knowledge about SaaS customer success "
            "tools, software, and best practices. Answer the user's question thoroughly and "
            "helpfully, recommending specific tools and platforms you know about. "
            "Include product names, pricing tiers if known, and use cases. "
            "Be balanced and mention multiple options. Do not fabricate tools - "
            "only mention ones you genuinely know exist."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text

    async def _analyze_response(self, prompt: str, response: str) -> Dict[str, Any]:
        """
        Analyze an AI response to extract Successifier mentions, competitor mentions,
        rank, sentiment, and cited sources.
        """
        analysis_prompt = f"""Analyze this AI assistant response about customer success tools.

USER QUESTION: {prompt}

AI RESPONSE:
{response}

Extract the following and respond as valid JSON:
{{
  "successifier_mentioned": <true/false>,
  "mention_rank": <null or integer - 1 if mentioned first, 2 if second, etc.>,
  "mention_context": <null or brief quote/description of how Successifier is mentioned>,
  "mention_sentiment": <null or "positive"/"neutral"/"negative">,
  "competitors_mentioned": [
    {{"name": "<tool name, lowercase>", "rank": <integer>, "context": "<brief context>"}}
  ],
  "sources_cited": ["<url or domain if any source is mentioned>"]
}}

Competitors to specifically look for: {', '.join(TRACKED_COMPETITORS)}.
Also flag any other customer success tools mentioned.
Mention rank = order in which tools are recommended (1 = first recommended).
Return ONLY valid JSON, no other text."""

        response_obj = self.client.messages.create(
            model=self.model,
            max_tokens=600,
            messages=[{"role": "user", "content": analysis_prompt}],
        )

        raw = response_obj.content[0].text.strip()

        # Extract JSON if wrapped in markdown code block
        json_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        if json_match:
            raw = json_match.group(1)

        import json
        try:
            return json.loads(raw)
        except Exception:
            # Fallback: check for simple mention
            mentioned = "successifier" in response.lower()
            return {
                "successifier_mentioned": mentioned,
                "mention_rank": None,
                "mention_context": None,
                "mention_sentiment": None,
                "competitors_mentioned": [],
                "sources_cited": [],
            }

    def _identify_gap(self, check: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Given a check where Successifier was NOT mentioned,
        identify the gap and recommend an action.
        """
        competitors = check.get("competitors_mentioned", [])
        top_competitor = competitors[0]["name"] if competitors else None

        category = check.get("prompt_category", "")

        action_map = {
            "competitor_alternative": (
                "create_content",
                f"Create a comparison page 'Successifier vs {top_competitor.title() if top_competitor else 'alternatives'}' "
                "targeting this exact query to build AI-indexable content.",
            ),
            "tool_recommendation": (
                "build_reviews",
                "Increase G2/Capterra reviews to boost authority signals that AI tools use for recommendations.",
            ),
            "use_case": (
                "optimize_page",
                "Create or optimize a landing page that directly answers this use case with concrete facts and proof points.",
            ),
            "buying_intent": (
                "forum_engagement",
                "Engage in Reddit/Quora discussions about this topic to build citation sources that AI tools reference.",
            ),
        }

        action_type, recommended_action = action_map.get(
            category,
            ("optimize_page", "Create targeted content for this query."),
        )

        priority = "high" if top_competitor in ["gainsight", "churnzero"] else "medium"

        return {
            "identified_at": datetime.utcnow().isoformat(),
            "prompt": check["prompt"],
            "prompt_category": category,
            "competitor_winning": top_competitor,
            "gap_type": "not_mentioned",
            "recommended_action": recommended_action,
            "action_type": action_type,
            "priority": priority,
            "status": "open",
        }

    async def get_visibility_summary(self) -> Dict[str, Any]:
        """
        Get aggregated visibility stats from the database.
        """
        sb = self._get_sb()

        # Recent checks (last 30 days)
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        checks = (
            sb.table(AI_VISIBILITY_CHECKS_TABLE)
            .select("*")
            .gte("checked_at", cutoff)
            .execute()
        )

        data = checks.data or []
        total = len(data)
        mentioned = sum(1 for c in data if c.get("successifier_mentioned"))
        ranks = [c["mention_rank"] for c in data if c.get("mention_rank")]

        # Open gaps
        gaps = (
            sb.table(AI_VISIBILITY_GAPS_TABLE)
            .select("*")
            .eq("status", "open")
            .execute()
        )

        # Competitor frequency
        competitor_counts: Dict[str, int] = {}
        for check in data:
            for comp in check.get("competitors_mentioned") or []:
                name = comp.get("name", "")
                competitor_counts[name] = competitor_counts.get(name, 0) + 1

        return {
            "period": "last_30_days",
            "total_checks": total,
            "successifier_mentions": mentioned,
            "mention_rate": round(mentioned / total, 2) if total else 0,
            "avg_mention_rank": round(sum(ranks) / len(ranks), 1) if ranks else None,
            "open_gaps": len(gaps.data or []),
            "top_competitors_in_ai": dict(
                sorted(competitor_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
        }

    async def get_open_gaps(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return open visibility gaps ordered by priority."""
        sb = self._get_sb()
        result = (
            sb.table(AI_VISIBILITY_GAPS_TABLE)
            .select("*")
            .eq("status", "open")
            .order("identified_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def get_recent_checks(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return most recent monitoring checks."""
        sb = self._get_sb()
        result = (
            sb.table(AI_VISIBILITY_CHECKS_TABLE)
            .select("*")
            .order("checked_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def generate_geo_recommendations(self) -> Dict[str, Any]:
        """
        Use Claude to generate specific GEO recommendations based on
        recent visibility check patterns.
        """
        if not self.client:
            return {"recommendations": [], "error": "Anthropic API not configured"}

        summary = await self.get_visibility_summary()
        gaps = await self.get_open_gaps(limit=10)

        prompt = f"""You are a GEO (Generative Engine Optimization) expert.
Successifier is an AI-native customer success platform.

Current AI visibility metrics:
- Mention rate: {summary['mention_rate']*100:.0f}% of relevant AI queries
- Average mention rank when cited: {summary.get('avg_mention_rank', 'N/A')}
- Open visibility gaps: {summary['open_gaps']}
- Top competitors appearing in AI instead of us: {list(summary['top_competitors_in_ai'].keys())[:3]}

Top open gaps (queries where we're not mentioned):
{chr(10).join([f"- [{g['prompt_category']}] {g['prompt']} (competitor winning: {g.get('competitor_winning', 'unknown')})" for g in gaps[:5]])}

Generate 5 specific, actionable GEO recommendations to improve Successifier's visibility in AI answers.
For each recommendation, specify:
1. What to do (concrete action)
2. Why it affects AI recommendations
3. Which gap(s) it addresses
4. Estimated impact (high/medium/low)

Focus on: content creation, review platform optimization, citation building, structured data, and Q&A content."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "summary": summary,
            "recommendations": response.content[0].text,
        }

    async def close_gap(self, gap_id: str, status: str = "resolved") -> Dict[str, Any]:
        """Mark a gap as resolved or in_progress."""
        sb = self._get_sb()
        result = (
            sb.table(AI_VISIBILITY_GAPS_TABLE)
            .update({"status": status})
            .eq("id", gap_id)
            .execute()
        )
        return {"success": True, "gap_id": gap_id, "new_status": status}

    async def _save_check(self, check: Dict[str, Any]) -> None:
        """Persist a visibility check to Supabase."""
        try:
            sb = self._get_sb()
            sb.table(AI_VISIBILITY_CHECKS_TABLE).insert(check).execute()
        except Exception as e:
            logger.warning(f"⚠️ Could not save visibility check: {e}")

    async def _save_gap(self, gap: Dict[str, Any]) -> None:
        """Persist a gap to Supabase."""
        try:
            sb = self._get_sb()
            sb.table(AI_VISIBILITY_GAPS_TABLE).insert(gap).execute()
        except Exception as e:
            logger.warning(f"⚠️ Could not save visibility gap: {e}")

    def _empty_check(self, prompt: str, category: str) -> Dict[str, Any]:
        """Fallback check when API is not configured."""
        return {
            "prompt": prompt,
            "prompt_category": category,
            "checked_at": datetime.utcnow().isoformat(),
            "ai_response": None,
            "successifier_mentioned": False,
            "mention_rank": None,
            "mention_context": None,
            "mention_sentiment": None,
            "competitors_mentioned": [],
            "sources_cited": [],
            "check_source": "not_configured",
        }


# Singleton instance
ai_visibility_agent = AIVisibilityAgent()
