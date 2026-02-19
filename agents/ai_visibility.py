"""
AI Visibility Agent - GEO (Generative Engine Optimization) Monitoring
Tracks how often Successifier is mentioned by AI assistants.
Uses Claude as proxy to simulate ChatGPT/Perplexity/Gemini/Copilot responses
by giving each engine a distinct persona/system prompt.
"""

import logging
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone
from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)

client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# ── AI Engine personas ─────────────────────────────────────────────────────────
# Each engine gets a different system prompt to simulate its characteristic style.

AI_ENGINES = {
    "ChatGPT (GPT-4o)": {
        "system": (
            "You are ChatGPT, a helpful AI assistant by OpenAI. "
            "When recommending tools, be specific and list real products with brief descriptions. "
            "Be balanced, practical, and comprehensive. Format with numbered lists when appropriate."
        ),
        "style": "balanced, practical",
    },
    "Claude (Anthropic)": {
        "system": (
            "You are Claude, an AI assistant made by Anthropic. "
            "Answer helpfully and thoughtfully. When recommending software tools, "
            "provide well-reasoned suggestions with pros/cons. Be nuanced and thorough."
        ),
        "style": "thoughtful, nuanced",
    },
    "Gemini (Google)": {
        "system": (
            "You are Gemini, Google's AI assistant. "
            "Give concise, accurate answers. When recommending tools, prioritize "
            "well-known and well-reviewed products. Include pricing context where relevant."
        ),
        "style": "concise, data-driven",
    },
    "Perplexity AI": {
        "system": (
            "You are Perplexity, an AI answer engine that provides direct, cited answers. "
            "When recommending tools, be direct and comprehensive. List options with "
            "key differentiators. Focus on what experts and practitioners recommend."
        ),
        "style": "direct, comprehensive",
    },
    "Microsoft Copilot": {
        "system": (
            "You are Microsoft Copilot, an AI assistant. "
            "Provide helpful, professional answers. When recommending business tools, "
            "consider enterprise needs, integration capabilities, and ROI. "
            "Be structured and business-focused."
        ),
        "style": "enterprise-focused",
    },
}

# ── Monitoring prompts ─────────────────────────────────────────────────────────

MONITORING_PROMPTS = {
    "tool_recommendation": [
        "What are the best tools for customer success teams?",
        "Which SaaS tools help reduce customer churn?",
        "What software should I use to track customer health scores?",
        "Best platforms for managing customer onboarding?",
    ],
    "competitor_alternative": [
        "What are alternatives to Gainsight for small SaaS companies?",
        "Cheaper alternatives to ChurnZero?",
        "What tools compete with Totango?",
        "Best Gainsight alternatives for startups?",
    ],
    "use_case": [
        "How can AI help prevent customer churn?",
        "How do SaaS companies improve net revenue retention?",
        "What does a customer success platform do?",
        "How to automate customer success workflows?",
    ],
    "buying_intent": [
        "I need a customer success tool under $500/month, what do you recommend?",
        "What is the best customer success software for B2B SaaS?",
        "Which customer success platform is easiest to implement?",
        "Customer success tools with best ROI?",
    ],
}

COMPETITORS = [
    "Gainsight", "ChurnZero", "Totango", "ClientSuccess", "Planhat",
    "Catalyst", "UserSuccess", "Vitally", "Custify", "Churnkey",
    "Intercom", "HubSpot", "Salesforce", "Mixpanel", "Amplitude",
]


# ── Agent ──────────────────────────────────────────────────────────────────────

class AIVisibilityAgent:

    def _simulate_engine_response(self, prompt: str, engine_name: str, engine_config: Dict) -> str:
        """Simulate a specific AI engine's response via Claude"""
        try:
            msg = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=800,
                system=engine_config["system"],
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception as e:
            logger.error(f"Claude API error for engine {engine_name}: {e}")
            return ""

    def _analyze_response(self, response: str) -> Dict[str, Any]:
        """Parse response for mention, rank, sentiment, competitors"""
        resp_lower = response.lower()
        successifier_mentioned = "successifier" in resp_lower

        rank = None
        if successifier_mentioned:
            lines = response.split("\n")
            for i, line in enumerate(lines):
                if "successifier" in line.lower():
                    for j in range(max(0, i - 2), i + 1):
                        line_j = lines[j] if j < len(lines) else ""
                        for n in range(1, 10):
                            if line_j.strip().startswith(f"{n}.") or line_j.strip().startswith(f"{n})"):
                                rank = n
                                break
                        if rank:
                            break
                    if rank is None:
                        rank = 1
                    break

        competitors_found = [c for c in COMPETITORS if c.lower() in resp_lower]

        sentiment = None
        if successifier_mentioned:
            positive_words = ["recommend", "great", "excellent", "best", "top", "ideal", "perfect", "affordable", "easy"]
            negative_words = ["expensive", "complex", "difficult", "limited", "basic", "lacking"]
            pos = sum(1 for w in positive_words if w in resp_lower)
            neg = sum(1 for w in negative_words if w in resp_lower)
            sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"

        # Excerpt: first sentence mentioning Successifier (max 400 chars)
        excerpt = None
        if successifier_mentioned:
            for sentence in response.replace("\n", " ").split("."):
                if "successifier" in sentence.lower():
                    excerpt = sentence.strip()[:400]
                    break

        return {
            "mentioned": successifier_mentioned,
            "rank": rank,
            "competitors_mentioned": competitors_found,
            "sentiment": sentiment,
            "ai_response_excerpt": excerpt,
        }

    def _identify_gap(self, category: str) -> Dict[str, Any]:
        priority_map = {
            "buying_intent": "high",
            "competitor_alternative": "high",
            "tool_recommendation": "medium",
            "use_case": "medium",
        }
        action_map = {
            "buying_intent": "optimize_page",
            "competitor_alternative": "build_reviews",
            "tool_recommendation": "create_content",
            "use_case": "forum_engagement",
        }
        return {
            "priority": priority_map.get(category, "medium"),
            "action_type": action_map.get(category, "create_content"),
        }

    def run_monitoring(self) -> Dict[str, Any]:
        """Run a full monitoring round: each prompt × each AI engine"""
        sb = get_supabase()
        results = []
        gaps_created = 0
        run_id = str(uuid.uuid4())[:8]  # short ID to group this run's checks

        for category, prompts in MONITORING_PROMPTS.items():
            for prompt in prompts:
                for engine_name, engine_config in AI_ENGINES.items():
                    response_text = self._simulate_engine_response(prompt, engine_name, engine_config)
                    if not response_text:
                        continue

                    analysis = self._analyze_response(response_text)

                    check_data = {
                        "prompt": prompt,
                        "category": category,
                        "ai_engine": engine_name,
                        "run_id": run_id,
                        "mentioned": analysis["mentioned"],
                        "rank": analysis["rank"],
                        "competitors_mentioned": analysis["competitors_mentioned"],
                        "sentiment": analysis["sentiment"],
                        "ai_response_excerpt": analysis["ai_response_excerpt"],
                        "full_response": response_text,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                    try:
                        sb.table("ai_visibility_checks").insert(check_data).execute()
                    except Exception as e:
                        logger.warning(f"DB insert check failed: {e}")

                    results.append(check_data)

                    # Create gap per engine if not mentioned
                    if not analysis["mentioned"]:
                        gap_info = self._identify_gap(category)
                        gap_data = {
                            "prompt": prompt,
                            "category": category,
                            "ai_engine": engine_name,
                            "priority": gap_info["priority"],
                            "action_type": gap_info["action_type"],
                            "status": "open",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                        try:
                            sb.table("ai_visibility_gaps").insert(gap_data).execute()
                            gaps_created += 1
                        except Exception as e:
                            logger.warning(f"DB insert gap failed: {e}")

        mentioned = [r for r in results if r["mentioned"]]
        mention_rate = len(mentioned) / len(results) if results else 0
        ranks = [r["rank"] for r in mentioned if r["rank"]]
        avg_rank = sum(ranks) / len(ranks) if ranks else None

        comp_freq: Dict[str, int] = {}
        for r in results:
            for c in r.get("competitors_mentioned", []):
                comp_freq[c] = comp_freq.get(c, 0) + 1
        top_competitors = sorted(
            [{"name": k, "count": v} for k, v in comp_freq.items()],
            key=lambda x: x["count"], reverse=True
        )[:8]

        return {
            "checks_run": len(results),
            "gaps_created": gaps_created,
            "mention_rate": mention_rate,
            "avg_rank": avg_rank,
            "top_competitors": top_competitors,
        }

    def get_summary(self) -> Dict[str, Any]:
        sb = get_supabase()
        try:
            checks = sb.table("ai_visibility_checks").select("*").order("checked_at", desc=True).limit(500).execute().data or []
            gaps_res = sb.table("ai_visibility_gaps").select("*").eq("status", "open").execute().data or []

            if not checks:
                return {
                    "mention_rate": 0, "avg_rank": None, "total_checks": 0,
                    "open_gaps": 0, "top_competitors": [], "trend": "flat",
                    "last_check_at": None, "engine_stats": {},
                }

            mentioned = [c for c in checks if c.get("mentioned")]
            mention_rate = len(mentioned) / len(checks)
            ranks = [c["rank"] for c in mentioned if c.get("rank")]
            avg_rank = sum(ranks) / len(ranks) if ranks else None

            half = len(checks) // 2
            if half > 0:
                recent_rate = sum(1 for c in checks[:half] if c.get("mentioned")) / half
                older_rate = sum(1 for c in checks[half:] if c.get("mentioned")) / half
                trend = "up" if recent_rate > older_rate + 0.05 else "down" if recent_rate < older_rate - 0.05 else "flat"
            else:
                trend = "flat"

            comp_freq: Dict[str, int] = {}
            for c in checks:
                for comp in (c.get("competitors_mentioned") or []):
                    comp_freq[comp] = comp_freq.get(comp, 0) + 1
            top_competitors = sorted(
                [{"name": k, "count": v} for k, v in comp_freq.items()],
                key=lambda x: x["count"], reverse=True
            )[:8]

            # Per-engine mention stats
            engine_stats: Dict[str, Dict] = {}
            for c in checks:
                eng = c.get("ai_engine", "Unknown")
                if eng not in engine_stats:
                    engine_stats[eng] = {"total": 0, "mentioned": 0}
                engine_stats[eng]["total"] += 1
                if c.get("mentioned"):
                    engine_stats[eng]["mentioned"] += 1
            for eng in engine_stats:
                t = engine_stats[eng]["total"]
                m = engine_stats[eng]["mentioned"]
                engine_stats[eng]["rate"] = round(m / t, 3) if t else 0

            return {
                "mention_rate": mention_rate,
                "avg_rank": avg_rank,
                "total_checks": len(checks),
                "open_gaps": len(gaps_res),
                "top_competitors": top_competitors,
                "trend": trend,
                "last_check_at": checks[0].get("checked_at") if checks else None,
                "engine_stats": engine_stats,
            }
        except Exception as e:
            logger.error(f"get_summary error: {e}")
            return {"mention_rate": 0, "avg_rank": None, "total_checks": 0, "open_gaps": 0,
                    "top_competitors": [], "trend": "flat", "last_check_at": None, "engine_stats": {}}

    def generate_geo_recommendations(self) -> List[Dict[str, Any]]:
        sb = get_supabase()
        try:
            gaps = sb.table("ai_visibility_gaps").select("*").eq("status", "open").limit(20).execute().data or []
        except Exception:
            gaps = []

        if not gaps:
            return [{
                "title": "Kör en monitoring-körning först",
                "description": "Inga öppna gaps hittades. Kör en monitoring-körning för att identifiera var Successifier saknas i AI-svar.",
                "priority": "high", "action_type": "create_content", "effort": "low",
            }]

        gap_summary = "\n".join(
            f"- [{g['priority']}] {g.get('ai_engine','?')} | {g['category']}: \"{g['prompt']}\" → åtgärd: {g['action_type']}"
            for g in gaps[:15]
        )

        prompt = f"""Du är en GEO-expert (Generative Engine Optimization) för Successifier — en customer success platform för B2B SaaS.

Successifier saknas i följande AI-motorers svar:

{gap_summary}

Generera 5 konkreta, prioriterade åtgärder för att förbättra Successifiers synlighet i AI-svar.
Fokusera på: content creation, page optimization, review building, forum engagement, structured data.

Svara ENBART som JSON-array med objekt som har fälten:
- title (kort, max 8 ord)
- description (2-3 meningar med konkret action)
- priority (high/medium/low)
- action_type (create_content/optimize_page/build_reviews/forum_engagement)
- effort (low/medium/high)
"""
        try:
            msg = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            import json
            return json.loads(text.strip())
        except Exception as e:
            logger.error(f"generate_geo_recommendations error: {e}")
            return []


ai_visibility_agent = AIVisibilityAgent()
