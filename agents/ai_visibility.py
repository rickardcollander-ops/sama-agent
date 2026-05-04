"""
AI Visibility Agent - GEO (Generative Engine Optimization) Monitoring
Tracks how often Successifier is mentioned by AI assistants.

- Perplexity AI: queried via the real Perplexity API (api.perplexity.ai).
- Other engines (ChatGPT, Gemini, Copilot): Claude is used as a simulation
  proxy since direct API access isn't available for those engines.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone

import httpx
from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)

# Module-level fallback client used by legacy callers that still touch the
# default `ai_visibility_agent` singleton. Per-tenant runs use `self.client`
# constructed in __init__ from tenant_config.
client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None

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

    def __init__(self, tenant_config=None):
        self.tenant_config = tenant_config
        api_key = (
            tenant_config.anthropic_api_key
            if tenant_config and tenant_config.anthropic_api_key
            else settings.ANTHROPIC_API_KEY
        )
        self.client = Anthropic(api_key=api_key) if api_key else None

    async def run_cycle(self) -> str:
        """Standard cycle entry point used by the trigger endpoint and scheduler."""
        result = await self.run_monitoring()
        if isinstance(result, dict):
            mention_rate = result.get("mention_rate")
            checks = result.get("total_checks") or len(result.get("results", []))
            if mention_rate is not None:
                return f"{checks} checks, {mention_rate}% mention rate"
            return f"{checks} checks completed"
        return "GEO check completed"

    async def _query_engine(self, prompt: str, engine_name: str, engine_config: Dict) -> str:
        """Query an AI engine for its response to *prompt*.

        - Perplexity AI: uses the real Perplexity chat completions API.
        - All other engines: Claude is used as a simulation proxy since direct
          API access isn't available for those engines.
        """
        if engine_name == "Perplexity AI" and settings.PERPLEXITY_API_KEY:
            return await self._query_perplexity(prompt)

        # Fallback: simulate via Claude (proxy for engines without direct API access)
        return await self._query_engine_via_claude(prompt, engine_name, engine_config)

    async def _query_perplexity(self, prompt: str) -> str:
        """Call the real Perplexity API (api.perplexity.ai) using httpx."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "sonar",
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Perplexity API error: {e}")
            return ""

    async def _query_engine_via_claude(self, prompt: str, engine_name: str, engine_config: Dict) -> str:
        """Simulate an engine response via Claude (used as proxy when direct API access is unavailable)."""
        try:
            msg = await asyncio.to_thread(
                self.client.messages.create,
                model=settings.CLAUDE_MODEL,
                max_tokens=800,
                system=engine_config["system"],
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception as e:
            logger.error(f"Claude API error for engine {engine_name}: {e}")
            return ""

    def _brand_token(self) -> str:
        """Lower-case brand name to scan for in AI responses."""
        if self.tenant_config and getattr(self.tenant_config, "brand_name", None):
            return self.tenant_config.brand_name.strip().lower()
        return "successifier"

    def _competitors(self) -> List[str]:
        """Tenant competitors when configured, otherwise the legacy global list."""
        if self.tenant_config:
            comp = self.tenant_config.competitors
            if comp:
                return list(comp)
        return list(COMPETITORS)

    def _analyze_response(self, response: str) -> Dict[str, Any]:
        """Parse response for mention, rank, sentiment, competitors"""
        resp_lower = response.lower()
        brand = self._brand_token()
        brand_mentioned = bool(brand) and brand in resp_lower

        rank = None
        if brand_mentioned:
            lines = response.split("\n")
            for i, line in enumerate(lines):
                if brand in line.lower():
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

        competitors_found = [c for c in self._competitors() if c.lower() in resp_lower]

        sentiment = None
        if brand_mentioned:
            positive_words = ["recommend", "great", "excellent", "best", "top", "ideal", "perfect", "affordable", "easy"]
            negative_words = ["expensive", "complex", "difficult", "limited", "basic", "lacking"]
            pos = sum(1 for w in positive_words if w in resp_lower)
            neg = sum(1 for w in negative_words if w in resp_lower)
            sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"

        # Excerpt: first sentence mentioning the brand (max 400 chars)
        excerpt = None
        if brand_mentioned:
            for sentence in response.replace("\n", " ").split("."):
                if brand in sentence.lower():
                    excerpt = sentence.strip()[:400]
                    break

        return {
            "mentioned": brand_mentioned,
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

    def _build_prompt_categories(self) -> Dict[str, List[str]]:
        """Return the prompts to monitor.

        When the tenant has saved GEO queries via the dashboard, those drive
        the run (categorised as ``user_query``). Otherwise we fall back to the
        legacy hard-coded MONITORING_PROMPTS so default/test runs still work.
        """
        if self.tenant_config and self.tenant_config.geo_queries:
            return {"user_query": list(self.tenant_config.geo_queries)}
        return MONITORING_PROMPTS

    async def run_monitoring(self) -> Dict[str, Any]:
        """Run a full monitoring round: each prompt x each AI engine.

        All engines for a given prompt are queried concurrently via asyncio.gather.
        """
        sb = get_supabase()
        results: List[Dict[str, Any]] = []
        gaps_created = 0
        run_id = str(uuid.uuid4())[:8]  # short ID to group this run's checks
        tenant_id = self.tenant_config.tenant_id if self.tenant_config else None
        prompt_categories = self._build_prompt_categories()

        for category, prompts in prompt_categories.items():
            for prompt in prompts:
                # Query all 5 engines for this prompt in parallel
                engine_items = list(AI_ENGINES.items())
                responses = await asyncio.gather(
                    *(self._query_engine(prompt, name, cfg) for name, cfg in engine_items)
                )

                for (engine_name, engine_config), response_text in zip(engine_items, responses):
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
                    if tenant_id:
                        check_data["tenant_id"] = tenant_id
                    try:
                        sb.table("ai_visibility_checks").insert(check_data).execute()
                        logger.info(f"[{run_id}] {engine_name} | {category} | mentioned={analysis['mentioned']}")
                    except Exception as e:
                        logger.error(f"[{run_id}] DB insert check FAILED: {e} | data keys: {list(check_data.keys())}")

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
                        if tenant_id:
                            gap_data["tenant_id"] = tenant_id
                        try:
                            sb.table("ai_visibility_gaps").insert(gap_data).execute()
                            gaps_created += 1
                        except Exception as e:
                            logger.error(f"[{run_id}] DB insert gap FAILED: {e}")

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

    async def generate_strategic_analysis(self) -> Dict[str, Any]:
        """Generate a comprehensive, opinionated strategic analysis of AI visibility."""
        sb = get_supabase()
        try:
            checks = sb.table("ai_visibility_checks").select("*").order("checked_at", desc=True).limit(500).execute().data or []
            gaps = sb.table("ai_visibility_gaps").select("*").eq("status", "open").limit(50).execute().data or []
        except Exception:
            checks = []
            gaps = []

        if not checks:
            return {
                "analysis": "No monitoring data available yet. Run a monitoring check first to generate a strategic analysis.",
                "verdict": "unknown",
                "priority_actions": [],
            }

        # Build rich context for Claude
        summary = self.get_summary()

        # Per-engine breakdown
        engine_lines = []
        for eng, stats in summary.get("engine_stats", {}).items():
            engine_lines.append(f"  {eng}: {stats['mentioned']}/{stats['total']} mentioned ({round(stats['rate']*100)}%)")

        # Category breakdown
        cat_stats: Dict[str, Dict] = {}
        for c in checks:
            cat = c.get("category", "unknown")
            if cat not in cat_stats:
                cat_stats[cat] = {"total": 0, "mentioned": 0}
            cat_stats[cat]["total"] += 1
            if c.get("mentioned"):
                cat_stats[cat]["mentioned"] += 1
        cat_lines = []
        for cat, s in cat_stats.items():
            rate = round(s["mentioned"] / s["total"] * 100) if s["total"] else 0
            cat_lines.append(f"  {cat}: {s['mentioned']}/{s['total']} ({rate}%)")

        # Competitor frequency
        comp_lines = []
        for comp in summary.get("top_competitors", [])[:10]:
            comp_lines.append(f"  {comp['name']}: mentioned {comp['count']} times")

        # Gap patterns
        gap_by_engine: Dict[str, int] = {}
        gap_by_cat: Dict[str, int] = {}
        for g in gaps:
            eng = g.get("ai_engine", "?")
            cat = g.get("category", "?")
            gap_by_engine[eng] = gap_by_engine.get(eng, 0) + 1
            gap_by_cat[cat] = gap_by_cat.get(cat, 0) + 1

        # Sample AI responses where Successifier IS mentioned (for quality analysis)
        positive_excerpts = []
        for c in checks:
            if c.get("mentioned") and c.get("ai_response_excerpt"):
                positive_excerpts.append(f"  [{c.get('ai_engine')}] {c['ai_response_excerpt'][:200]}")
                if len(positive_excerpts) >= 5:
                    break

        # Sample AI responses where Successifier is NOT mentioned
        negative_samples = []
        for c in checks:
            if not c.get("mentioned") and c.get("full_response"):
                negative_samples.append(
                    f"  [{c.get('ai_engine')}] Prompt: \"{c.get('prompt')}\" → Response mentions: "
                    f"{', '.join(c.get('competitors_mentioned', [])[:5]) or 'none'}"
                )
                if len(negative_samples) >= 5:
                    break

        prompt = f"""You are a senior GEO (Generative Engine Optimization) strategist performing a comprehensive audit of Successifier's visibility in AI-powered search and assistant tools.

Successifier is a customer success platform for B2B SaaS companies. Your job is to deliver a BOLD, OPINIONATED analysis — not generic advice. Be direct about what's working, what's failing, and what the highest-leverage moves are. Think like a CMO who needs to 10x AI visibility within 90 days.

## CURRENT DATA

**Overall mention rate**: {round(summary.get('mention_rate', 0) * 100)}% ({summary.get('total_checks', 0)} total checks)
**Average rank when mentioned**: {summary.get('avg_rank', 'N/A')}
**Open gaps**: {len(gaps)}
**Trend**: {summary.get('trend', 'flat')}

### Per-engine performance:
{chr(10).join(engine_lines) or '  No data'}

### Per-category performance:
{chr(10).join(cat_lines) or '  No data'}

### Top competitors appearing in AI responses:
{chr(10).join(comp_lines) or '  No data'}

### Gap distribution by engine:
{chr(10).join(f'  {k}: {v} gaps' for k, v in sorted(gap_by_engine.items(), key=lambda x: -x[1])) or '  No gaps'}

### Gap distribution by category:
{chr(10).join(f'  {k}: {v} gaps' for k, v in sorted(gap_by_cat.items(), key=lambda x: -x[1])) or '  No gaps'}

### How Successifier is described when mentioned:
{chr(10).join(positive_excerpts) or '  No positive mentions yet'}

### Where Successifier is missing (sample):
{chr(10).join(negative_samples) or '  No negative samples'}

## YOUR TASK

Write a strategic analysis in JSON format with these fields:

1. **verdict** (string): One of "critical", "weak", "improving", "strong" — your overall assessment
2. **headline** (string): A bold, one-sentence summary (max 15 words). Be provocative.
3. **analysis** (string): 3-5 paragraphs of strategic analysis in markdown. Cover:
   - The brutal truth about current visibility
   - Which engines are the biggest opportunity vs lost cause (for now)
   - How competitors are eating Successifier's lunch and what patterns you see
   - The #1 structural problem holding back visibility
   - What the 90-day transformation plan should look like
4. **priority_actions** (array of objects): Exactly 5 actions, each with:
   - title (string, max 10 words)
   - description (string, 2-3 sentences, concrete and specific)
   - impact (string): "high" / "medium" / "low"
   - effort (string): "low" / "medium" / "high"
   - timeline (string): e.g. "Week 1-2", "Month 1", "Ongoing"
   - engine_target (string): which engine(s) this primarily targets, or "all"
5. **blind_spots** (array of strings): 3 things the current monitoring might be missing or undervaluing
6. **competitor_insight** (string): 1-2 paragraphs about what the top competitors are doing right that Successifier can learn from

Be specific to THIS data. No generic GEO advice. Reference actual numbers, engines, and patterns from the data above.

Respond ONLY with valid JSON (no markdown fences).
"""
        try:
            msg = await asyncio.to_thread(
                self.client.messages.create,
                model=settings.CLAUDE_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())
        except Exception as e:
            logger.error(f"generate_strategic_analysis error: {e}")
            return {
                "analysis": f"Failed to generate analysis: {str(e)}",
                "verdict": "unknown",
                "priority_actions": [],
            }

    async def generate_geo_recommendations(self) -> List[Dict[str, Any]]:
        sb = get_supabase()
        try:
            gaps = sb.table("ai_visibility_gaps").select("*").eq("status", "open").limit(20).execute().data or []
        except Exception:
            gaps = []

        if not gaps:
            return [{
                "title": "Run a monitoring check first",
                "description": "No open gaps found. Run a monitoring check to identify where Successifier is missing from AI responses.",
                "priority": "high", "action_type": "create_content", "effort": "low",
            }]

        gap_summary = "\n".join(
            f"- [{g['priority']}] {g.get('ai_engine','?')} | {g['category']}: \"{g['prompt']}\" → action: {g['action_type']}"
            for g in gaps[:15]
        )

        prompt = f"""You are a GEO expert (Generative Engine Optimization) for Successifier — a customer success platform for B2B SaaS.

Successifier is missing from the following AI engines' responses:

{gap_summary}

Generate 5 concrete, prioritized actions to improve Successifier's visibility in AI responses.
Focus on: content creation, page optimization, review building, forum engagement, structured data.

Respond ONLY as a JSON array with objects having these fields:
- title (short, max 8 words)
- description (2-3 sentences with concrete action)
- priority (high/medium/low)
- action_type (create_content/optimize_page/build_reviews/forum_engagement)
- effort (low/medium/high)
"""
        try:
            msg = await asyncio.to_thread(
                self.client.messages.create,
                model=settings.CLAUDE_MODEL,
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())
        except Exception as e:
            logger.error(f"generate_geo_recommendations error: {e}")
            return []


ai_visibility_agent = AIVisibilityAgent()
