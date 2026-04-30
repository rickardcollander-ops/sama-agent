"""
Analysis Agent — SEO + GEO unified visibility (roadmap P2.9).

Runs a list of buyer-intent queries through:
  - Google SERP (via ValueSERP) to get the brand's organic rank + competitor
    presence + AI Overview if available.
  - Multiple AI assistants (ChatGPT-as-Claude, Claude, Perplexity, Gemini-as-Claude,
    Google AIO via Perplexity, Copilot-as-Claude) to measure mention rate, ordinal
    rank, source citation, sentiment.

Each query is bucketed into a gap category and the result is persisted as a
JSONB blob in analysis_runs. The shape exactly matches the TypeScript
AnalysisRun in app/c/analysis/types.ts so the dashboard can render it
unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)


# Default platforms when caller doesn't specify. Matches frontend defaults.
DEFAULT_PLATFORMS = ["chatgpt", "claude", "perplexity", "google_aio"]

# Persona prompts for AI engines we don't have direct API access to. We use
# Claude as a proxy with engine-specific system prompts (same approach as the
# existing AIVisibilityAgent so output style matches the engine's character).
_ENGINE_PERSONAS = {
    "chatgpt": (
        "You are ChatGPT by OpenAI. When recommending tools or comparing options, "
        "be specific and list real products with brief descriptions. "
        "Be balanced, practical, and comprehensive."
    ),
    "claude": (
        "You are Claude by Anthropic. Answer thoughtfully. When recommending tools, "
        "give well-reasoned suggestions with pros/cons. Be nuanced and thorough."
    ),
    "gemini": (
        "You are Gemini by Google. Give concise, accurate answers. "
        "When recommending tools, prioritize popular and well-supported options."
    ),
    "google_aio": (
        "You are Google's AI Overview. Provide a brief, factual summary that "
        "would appear at the top of a search result page. Cite sources."
    ),
    "copilot": (
        "You are Microsoft Copilot. Be concise, practical, and integrate well "
        "with productivity workflows."
    ),
    "perplexity": (
        # Used only as a fallback when the real Perplexity API is unconfigured.
        "You are Perplexity AI. Give a sourced, well-cited answer. "
        "Format as a brief paragraph followed by a numbered list of sources."
    ),
}


class AnalysisAgent:
    """Tenant-scoped orchestrator for SEO + GEO unified analysis."""

    def __init__(self, tenant_config=None):
        self.tenant_config = tenant_config
        api_key = (
            tenant_config.anthropic_api_key
            if tenant_config and tenant_config.anthropic_api_key
            else settings.ANTHROPIC_API_KEY
        )
        self.client = Anthropic(api_key=api_key) if api_key else None
        self.model = "claude-sonnet-4-20250514"
        self.serp_key = settings.VALUESERP_API_KEY
        self.perplexity_key = settings.PERPLEXITY_API_KEY
        self.brand_name = (tenant_config.brand_name if tenant_config else "Successifier") or "Brand"
        self.domain = (tenant_config.domain if tenant_config else "successifier.com") or ""
        self.competitors = list(tenant_config.competitors) if tenant_config else []

    # ── Public API ──────────────────────────────────────────────────────────

    async def generate_queries(self, count: int = 10) -> List[str]:
        """
        Use the LLM to generate `count` buyer-intent queries from the tenant's
        brand context. Falls back to deterministic templates if the LLM is
        unavailable.
        """
        if not self.client:
            return self._fallback_queries(count)

        usp = ""
        audience = ""
        description = ""
        if self.tenant_config:
            usp = (self.tenant_config.get_raw("unique_selling_points") or "").strip()
            audience = (self.tenant_config.get_raw("target_audience") or "").strip()
            description = (self.tenant_config.get_raw("brand_description") or "").strip()

        prompt = f"""Generate {count} buyer-intent search queries that prospects would ask Google or AI assistants when looking for a tool like {self.brand_name}.

Brand: {self.brand_name}
Domain: {self.domain}
Description: {description or "—"}
Target audience: {audience or "—"}
USP: {usp or "—"}

Mix:
- Comparison ("X vs Y", "best alternative to Y")
- Discovery ("best tool for ...", "top tools for ...")
- Decision ("is X worth it", "X pricing", "X reviews")
- Use-case ("how to ...", "tool that does ...")

Respond with JSON only — an array of {count} strings. No prose, no markdown fences.
""".strip()

        try:
            msg = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
            queries = json.loads(text)
            if isinstance(queries, list):
                return [str(q) for q in queries if q][:count]
        except Exception as e:
            logger.warning(f"AnalysisAgent.generate_queries LLM call failed: {e}")
        return self._fallback_queries(count)

    async def run(self, queries: List[str], platforms: List[str]) -> Dict[str, Any]:
        """
        Run the full analysis. Returns a dict matching the TypeScript
        AnalysisRun shape (id is filled in by the caller from the DB row).
        """
        platforms = platforms or DEFAULT_PLATFORMS

        # Run all per-query work concurrently; one slow query shouldn't block
        # the others. Each helper handles its own errors.
        tasks = [self._analyze_query(q, platforms) for q in queries]
        query_results = await asyncio.gather(*tasks, return_exceptions=False)

        overview = self._build_overview(query_results)

        return {
            "id": "",  # filled by route after persistence
            "created_at": datetime.now(timezone.utc).isoformat(),
            "brand_name": self.brand_name,
            "domain": self.domain,
            "platforms": platforms,
            "query_results": query_results,
            "overview": overview,
            "status": "completed",
        }

    # ── Per-query analysis ──────────────────────────────────────────────────

    async def _analyze_query(self, query: str, platforms: List[str]) -> Dict[str, Any]:
        # SERP and AI calls fan out concurrently for one query.
        serp_task = self._google_serp(query)
        ai_tasks = [self._query_ai(query, p) for p in platforms]
        serp, *ai_results = await asyncio.gather(serp_task, *ai_tasks, return_exceptions=False)

        seo_rank = serp["seo_rank"]
        seo_competitors_in_top10 = serp["seo_competitors_in_top10"]
        gap = self._classify_gap(seo_rank, ai_results)

        return {
            "query": query,
            "seo_rank": seo_rank,
            "seo_competitors_in_top10": seo_competitors_in_top10,
            "ai_results": ai_results,
            "gap": gap,
        }

    async def _google_serp(self, query: str) -> Dict[str, Any]:
        """
        Fetch Google's top 10 for the query, locate the brand domain's rank,
        and count competitors in the top 10.
        """
        if not self.serp_key or not self.domain:
            return {"seo_rank": None, "seo_competitors_in_top10": 0}

        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.get(
                    "https://api.valueserp.com/search",
                    params={
                        "api_key": self.serp_key,
                        "q": query,
                        "google_domain": "google.com",
                        "num": 10,
                    },
                )
                if resp.status_code != 200:
                    return {"seo_rank": None, "seo_competitors_in_top10": 0}
                data = resp.json()
        except Exception as e:
            logger.warning(f"valueserp call failed for '{query}': {e}")
            return {"seo_rank": None, "seo_competitors_in_top10": 0}

        organic = (data or {}).get("organic_results") or []
        seo_rank: Optional[int] = None
        competitor_count = 0
        domain_root = self.domain.lower().lstrip("www.")
        comps_root = [c.lower().lstrip("www.") for c in self.competitors]

        for idx, item in enumerate(organic[:10], start=1):
            link = (item.get("link") or "").lower()
            if seo_rank is None and domain_root and domain_root in link:
                seo_rank = idx
            if any(c and c in link for c in comps_root):
                competitor_count += 1

        return {"seo_rank": seo_rank, "seo_competitors_in_top10": competitor_count}

    async def _query_ai(self, query: str, platform: str) -> Dict[str, Any]:
        """Get the AI engine's answer to the query and analyze for mentions."""
        try:
            if platform == "perplexity" and self.perplexity_key:
                response = await self._perplexity_call(query)
            else:
                response = await self._claude_proxy_call(query, platform)
        except Exception as e:
            logger.warning(f"AI call failed for {platform} / '{query}': {e}")
            response = ""

        return self._analyze_ai_response(platform, response)

    async def _perplexity_call(self, query: str) -> str:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.perplexity_key}",
                    "Content-Type": "application/json",
                },
                json={"model": "sonar", "messages": [{"role": "user", "content": query}]},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _claude_proxy_call(self, query: str, platform: str) -> str:
        if not self.client:
            return ""
        system = _ENGINE_PERSONAS.get(platform, _ENGINE_PERSONAS["claude"])
        msg = await asyncio.to_thread(
            self.client.messages.create,
            model=self.model,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": query}],
        )
        return msg.content[0].text or ""

    def _analyze_ai_response(self, platform: str, response: str) -> Dict[str, Any]:
        text = (response or "").lower()
        brand_lower = self.brand_name.lower()
        domain_lower = self.domain.lower().lstrip("www.")

        # Mention detection: brand name OR domain mentioned anywhere.
        mentioned = bool(brand_lower and brand_lower in text) or bool(domain_lower and domain_lower in text)

        # Rank: find the first paragraph index where the brand appears
        # (1-based) — proxies the "ordinal mention" requested by the spec.
        rank: Optional[int] = None
        if mentioned and brand_lower:
            paragraphs = [p for p in re.split(r"\n+", response or "") if p.strip()]
            for idx, para in enumerate(paragraphs, start=1):
                if brand_lower in para.lower():
                    rank = idx
                    break

        cited_as_source = bool(domain_lower) and domain_lower in text and re.search(
            rf"\b{re.escape(domain_lower)}\b", text
        ) is not None

        # Light sentiment heuristic — for production, swap with a proper LLM
        # classification but keep the field shape stable.
        sentiment: Optional[str] = None
        if mentioned:
            negatives = ["expensive", "limited", "difficult", "lacks", "not recommended", "avoid"]
            positives = ["recommend", "best", "leading", "great", "powerful", "popular", "top"]
            neg_hit = any(w in text for w in negatives)
            pos_hit = any(w in text for w in positives)
            sentiment = "negative" if neg_hit and not pos_hit else ("positive" if pos_hit else "neutral")

        competitors_mentioned = [c for c in self.competitors if c and c.lower() in text]

        return {
            "platform": platform,
            "mentioned": mentioned,
            "rank": rank,
            "cited_as_source": cited_as_source,
            "sentiment": sentiment,
            "competitors_mentioned": competitors_mentioned,
        }

    # ── Gap classification + overview ───────────────────────────────────────

    @staticmethod
    def _classify_gap(seo_rank: Optional[int], ai_results: List[Dict[str, Any]]) -> str:
        seo_strong = seo_rank is not None and seo_rank <= 10
        mentioned_count = sum(1 for r in ai_results if r.get("mentioned"))
        ai_strong = mentioned_count / max(len(ai_results), 1) >= 0.5
        competitor_strong = any(r.get("competitors_mentioned") for r in ai_results)

        if competitor_strong and not seo_strong and not ai_strong:
            return "competitor_dominates"
        if seo_strong and not ai_strong:
            return "seo_winner_geo_loser"
        if not seo_strong and ai_strong:
            return "geo_winner_seo_loser"
        if seo_strong and ai_strong:
            return "both_winners"
        return "both_losers"

    def _build_overview(self, query_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_queries = len(query_results)
        platform_count = max((len(q["ai_results"]) for q in query_results), default=0)
        total_slots = total_queries * platform_count

        total_mentions = sum(
            sum(1 for r in q["ai_results"] if r.get("mentioned"))
            for q in query_results
        )
        seo_top10 = sum(1 for q in query_results if q["seo_rank"] and q["seo_rank"] <= 10)
        present = sum(
            1 for q in query_results
            if (q["seo_rank"] and q["seo_rank"] <= 10) or any(r.get("mentioned") for r in q["ai_results"])
        )

        opportunities = [
            {
                "query": q["query"],
                "reason": (
                    "You rank on Google but AIs don't mention you — citation gap"
                    if q["gap"] == "seo_winner_geo_loser"
                    else "AIs mention you but Google doesn't rank you — backlink/pillar gap"
                ),
            }
            for q in query_results
            if q["gap"] in ("seo_winner_geo_loser", "geo_winner_seo_loser")
        ][:3]

        return {
            "overall_mention_rate": (total_mentions / total_slots) if total_slots else 0,
            "seo_top10_coverage": (seo_top10 / total_queries) if total_queries else 0,
            "queries_with_presence": present,
            "total_queries": total_queries,
            "top_opportunities": opportunities,
        }

    # ── Fallbacks ───────────────────────────────────────────────────────────

    def _fallback_queries(self, count: int) -> List[str]:
        seed = self.brand_name or "your brand"
        templates = [
            f"What is the best {seed} alternative?",
            f"{seed} vs competitors",
            f"How does {seed} compare to other tools in the market?",
            f"Top tools for B2B teams",
            f"Is {seed} worth it?",
            f"{seed} pricing and plans",
            f"{seed} reviews and ratings",
            f"Best alternatives to {seed}",
            f"{seed} use cases",
            f"Why choose {seed}?",
        ]
        return templates[:count]
