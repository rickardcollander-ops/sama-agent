"""
GTM (Go-To-Market) Strategy Agent
Sits above all other SAMA agents and the Growth Hub CRM.
Responsibilities:
- Define and refine ICP based on pipeline win/loss data + SAMA traffic data
- Coordinate cross-system strategy (SAMA awareness → CRM conversion)
- Generate targeting signals for content, ads, social agents
- Analyze pipeline performance and feed insights back to marketing
"""

import asyncio
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
import httpx
from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase, SupabaseDB
from .brand_voice import BrandVoice
from .models import GTM_ICP_TABLE, GTM_STRATEGIES_TABLE, GTM_SIGNALS_TABLE

logger = logging.getLogger(__name__)

# Growth Hub Bridge API
GROWTH_HUB_API = settings.LINKEDIN_AGENT_API_URL  # http://localhost:3003/api


class GTMAgent:
    """
    Go-To-Market Strategy Agent.
    Coordinates SAMA (awareness/inbound) with Growth Hub CRM (outbound/pipeline).
    """

    SYSTEM_PROMPT = """You are the GTM Strategy Agent for Successifier, an AI-native Customer Success Platform for B2B SaaS companies.

Your role is to define and optimize the go-to-market strategy by connecting two systems:
1. SAMA — handles inbound marketing (SEO, content, ads, social, reviews)
2. Growth Hub CRM — handles outbound sales (LinkedIn prospecting, outreach, pipeline)

You have access to:
- Pipeline data: prospects, win/loss rates, conversion by segment
- Marketing data: traffic, keyword rankings, content performance, ad ROAS
- Brand voice & ICP definitions

Your job is to:
1. ANALYZE which segments convert best (by title, industry, company size)
2. RECOMMEND where to focus marketing + outreach efforts
3. GENERATE targeting signals for other agents (keywords for SEO, audiences for ads, personas for outreach)
4. IDENTIFY gaps between what marketing attracts and what sales closes

Always be data-driven. Cite specific numbers. Provide actionable recommendations with clear priority (high/medium/low) and expected impact."""

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = settings.CLAUDE_MODEL
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.sb = None

    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb

    # ── Pipeline Data (from Growth Hub) ──────────────────────────────

    async def fetch_pipeline_stats(self) -> Dict[str, Any]:
        """Fetch pipeline statistics from Growth Hub bridge API"""
        try:
            headers = {"x-api-key": settings.GROWTH_HUB_BRIDGE_API_KEY}
            resp = await self.http_client.get(
                f"{GROWTH_HUB_API}/bridge/stats",
                headers=headers
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Bridge API returned {resp.status_code}")
            return {}
        except Exception as e:
            logger.warning(f"Could not reach Growth Hub: {e}")
            return {}

    async def fetch_prospects(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch prospects from Growth Hub CRM"""
        try:
            headers = {"x-api-key": settings.GROWTH_HUB_BRIDGE_API_KEY}
            params = {}
            if status:
                params["status"] = status
            resp = await self.http_client.get(
                f"{GROWTH_HUB_API}/bridge/prospects",
                headers=headers,
                params=params
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("prospects", data) if isinstance(data, dict) else data
            return []
        except Exception as e:
            logger.warning(f"Could not fetch prospects: {e}")
            return []

    # ── Marketing Data (from SAMA Supabase) ──────────────────────────

    async def fetch_marketing_metrics(self) -> Dict[str, Any]:
        """Gather key marketing metrics from SAMA's Supabase tables"""
        sb = self._get_sb()
        metrics = {}

        try:
            # Top keywords by clicks
            kw_result = sb.table("seo_keywords").select("keyword,current_position,current_clicks,current_impressions,current_ctr").order("current_clicks", desc=True).limit(20).execute()
            metrics["top_keywords"] = kw_result.data or []
        except Exception:
            metrics["top_keywords"] = []

        try:
            # Recent content performance
            content_result = sb.table("content_pieces").select("title,content_type,target_keyword,impressions_30d,clicks_30d,status").order("clicks_30d", desc=True).limit(10).execute()
            metrics["top_content"] = content_result.data or []
        except Exception:
            metrics["top_content"] = []

        try:
            # Latest daily metrics
            dm_result = sb.table("daily_metrics").select("*").order("date", desc=True).limit(7).execute()
            metrics["daily_metrics"] = dm_result.data or []
        except Exception:
            metrics["daily_metrics"] = []

        return metrics

    # ── ICP Analysis ─────────────────────────────────────────────────

    async def analyze_icp(self) -> Dict[str, Any]:
        """
        Analyze Ideal Customer Profile by combining pipeline win/loss data
        with marketing traffic data. Returns refined ICP with segments.
        """
        logger.info("🎯 Analyzing ICP from pipeline + marketing data...")

        # Gather data from both systems
        pipeline_stats = await self.fetch_pipeline_stats()
        won_prospects = await self.fetch_prospects(status="won")
        lost_prospects = await self.fetch_prospects(status="lost")
        demo_prospects = await self.fetch_prospects(status="demo_booked")
        marketing = await self.fetch_marketing_metrics()

        # Current ICP from brand voice
        current_icp = BrandVoice.TARGET_PERSONA

        # Ask Claude to analyze
        context = {
            "current_icp": current_icp,
            "pipeline_stats": pipeline_stats,
            "won_prospects": won_prospects[:50],
            "lost_prospects": lost_prospects[:50],
            "demo_booked": demo_prospects[:20],
            "marketing_metrics": marketing
        }

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"""Analyze our ICP based on this data:

Current ICP Definition:
{json.dumps(current_icp, indent=2)}

Pipeline Data:
- Stats: {json.dumps(pipeline_stats, indent=2, default=str)}
- Won deals ({len(won_prospects)}): {json.dumps(won_prospects[:20], indent=2, default=str)}
- Lost deals ({len(lost_prospects)}): {json.dumps(lost_prospects[:20], indent=2, default=str)}
- Demo booked ({len(demo_prospects)}): {json.dumps(demo_prospects[:10], indent=2, default=str)}

Marketing Data:
- Top keywords: {json.dumps(marketing.get('top_keywords', [])[:10], indent=2, default=str)}
- Top content: {json.dumps(marketing.get('top_content', [])[:5], indent=2, default=str)}

Provide your analysis as JSON with this structure:
{{
    "refined_icp": {{
        "primary_segment": {{
            "title": "...",
            "company_type": "...",
            "company_size": "...",
            "industry": "...",
            "pain_points": ["..."],
            "conversion_rate": "...",
            "confidence": "high/medium/low"
        }},
        "secondary_segments": [...],
        "insights": ["..."],
        "gaps": ["..."]
    }},
    "recommendations": [
        {{
            "action": "...",
            "target_agent": "seo|content|ads|social|linkedin|all",
            "priority": "high|medium|low",
            "expected_impact": "..."
        }}
    ]
}}"""
                }]
            )

        response = await asyncio.to_thread(_call)
        result = self._parse_json_response(response.content[0].text)

        # Store analysis
        await self._store_icp_analysis(result)

        logger.info("✅ ICP analysis complete")
        return result

    # ── GTM Strategy Generation ──────────────────────────────────────

    async def generate_strategy(self, focus: str = "full") -> Dict[str, Any]:
        """
        Generate GTM strategy with prioritized segments, channels, and messaging.

        Args:
            focus: "full" for complete strategy, or specific area like
                   "content", "outreach", "ads", "expansion"
        """
        logger.info(f"📋 Generating GTM strategy (focus: {focus})...")

        pipeline_stats = await self.fetch_pipeline_stats()
        marketing = await self.fetch_marketing_metrics()

        # Get latest ICP analysis
        latest_icp = await self._get_latest_icp()

        context = {
            "focus": focus,
            "icp": latest_icp or BrandVoice.TARGET_PERSONA,
            "pipeline": pipeline_stats,
            "marketing": marketing,
            "brand_voice": {
                "pillars": BrandVoice.MESSAGING_PILLARS,
                "proof_points": BrandVoice.PROOF_POINTS,
                "content_pillars": BrandVoice.CONTENT_PILLARS
            }
        }

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"""Generate a GTM strategy for Successifier.

Focus area: {focus}

ICP: {json.dumps(context['icp'], indent=2, default=str)}

Pipeline Performance:
{json.dumps(context['pipeline'], indent=2, default=str)}

Marketing Performance:
{json.dumps(context['marketing'], indent=2, default=str)}

Brand Voice & Pillars:
{json.dumps(context['brand_voice'], indent=2, default=str)}

Return as JSON:
{{
    "strategy_name": "...",
    "time_horizon": "30/60/90 days",
    "priority_segments": [
        {{
            "segment": "...",
            "rationale": "...",
            "channels": ["seo", "content", "ads", "linkedin", "social"],
            "messaging_angle": "...",
            "target_metrics": {{}}
        }}
    ],
    "channel_priorities": [
        {{
            "channel": "...",
            "priority": "high|medium|low",
            "actions": ["..."],
            "budget_allocation_pct": 0
        }}
    ],
    "content_themes": ["..."],
    "outreach_signals": {{
        "target_titles": ["..."],
        "target_industries": ["..."],
        "target_company_sizes": ["..."],
        "messaging_hooks": ["..."]
    }},
    "kpis": [
        {{
            "metric": "...",
            "current": "...",
            "target": "...",
            "timeframe": "..."
        }}
    ]
}}"""
                }]
            )

        response = await asyncio.to_thread(_call)
        result = self._parse_json_response(response.content[0].text)

        # Store strategy
        await self._store_strategy(result, focus)

        logger.info("✅ GTM strategy generated")
        return result

    # ── Cross-System Signals ─────────────────────────────────────────

    async def generate_signals(self) -> Dict[str, Any]:
        """
        Generate actionable signals for each agent based on GTM strategy
        and pipeline data. This is the bridge between strategy and execution.
        """
        logger.info("📡 Generating cross-system signals...")

        latest_strategy = await self._get_latest_strategy()
        latest_icp = await self._get_latest_icp()
        pipeline_stats = await self.fetch_pipeline_stats()

        if not latest_strategy:
            logger.info("No strategy found, generating one first...")
            latest_strategy = await self.generate_strategy()

        signals = {
            "generated_at": datetime.utcnow().isoformat(),
            "agents": {}
        }

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"""Based on our GTM strategy and pipeline data, generate specific signals for each agent.

Strategy: {json.dumps(latest_strategy, indent=2, default=str)}
ICP: {json.dumps(latest_icp, indent=2, default=str)}
Pipeline: {json.dumps(pipeline_stats, indent=2, default=str)}

Generate specific, actionable signals as JSON:
{{
    "seo_agent": {{
        "priority_keywords": ["..."],
        "content_gaps": ["..."],
        "competitor_focus": ["..."]
    }},
    "content_agent": {{
        "topics_to_create": ["..."],
        "angles_that_convert": ["..."],
        "content_types_needed": ["..."]
    }},
    "ads_agent": {{
        "audience_segments": ["..."],
        "messaging_hooks": ["..."],
        "budget_recommendations": ["..."]
    }},
    "social_agent": {{
        "themes": ["..."],
        "engagement_targets": ["..."],
        "posting_cadence": "..."
    }},
    "linkedin_agent": {{
        "search_queries": ["..."],
        "target_titles": ["..."],
        "target_industries": ["..."],
        "outreach_hooks": ["..."]
    }}
}}"""
                }]
            )

        response = await asyncio.to_thread(_call)
        result = self._parse_json_response(response.content[0].text)
        signals["agents"] = result

        # Store signals
        await self._store_signals(signals)

        # Publish to event bus for SAMA agents
        await self._publish_signals(signals)

        logger.info("✅ Signals generated and distributed")
        return signals

    # ── Performance Review ───────────────────────────────────────────

    async def review_performance(self) -> Dict[str, Any]:
        """
        Review GTM performance: what's working, what's not,
        and what should change.
        """
        logger.info("📊 Reviewing GTM performance...")

        pipeline_stats = await self.fetch_pipeline_stats()
        marketing = await self.fetch_marketing_metrics()
        latest_strategy = await self._get_latest_strategy()
        latest_icp = await self._get_latest_icp()

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"""Review our GTM performance and provide recommendations.

Current Strategy: {json.dumps(latest_strategy, indent=2, default=str)}
Current ICP: {json.dumps(latest_icp, indent=2, default=str)}
Pipeline Stats: {json.dumps(pipeline_stats, indent=2, default=str)}
Marketing Metrics: {json.dumps(marketing, indent=2, default=str)}

Provide a performance review as JSON:
{{
    "overall_health": "strong|good|needs_attention|critical",
    "score": 0-100,
    "working_well": [
        {{"area": "...", "evidence": "...", "recommendation": "double down"}}
    ],
    "needs_improvement": [
        {{"area": "...", "evidence": "...", "recommendation": "...", "priority": "high|medium|low"}}
    ],
    "strategic_pivots": [
        {{"from": "...", "to": "...", "rationale": "...", "expected_impact": "..."}}
    ],
    "next_actions": [
        {{"action": "...", "owner": "seo|content|ads|social|linkedin|gtm", "deadline": "...", "priority": "high|medium|low"}}
    ]
}}"""
                }]
            )

        response = await asyncio.to_thread(_call)
        result = self._parse_json_response(response.content[0].text)

        logger.info("✅ Performance review complete")
        return result

    # ── Helpers ───────────────────────────────────────────────────────

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Parse JSON from Claude's response"""
        import re
        try:
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed: {e}")
        return {"raw_response": text}

    async def _store_icp_analysis(self, analysis: Dict[str, Any]):
        """Store ICP analysis in Supabase"""
        try:
            await SupabaseDB.insert(GTM_ICP_TABLE, {
                "analysis": json.dumps(analysis, default=str),
                "created_at": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.warning(f"Could not store ICP analysis: {e}")

    async def _store_strategy(self, strategy: Dict[str, Any], focus: str):
        """Store strategy in Supabase"""
        try:
            await SupabaseDB.insert(GTM_STRATEGIES_TABLE, {
                "strategy": json.dumps(strategy, default=str),
                "focus": focus,
                "created_at": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.warning(f"Could not store strategy: {e}")

    async def _store_signals(self, signals: Dict[str, Any]):
        """Store signals in Supabase"""
        try:
            await SupabaseDB.insert(GTM_SIGNALS_TABLE, {
                "signals": json.dumps(signals, default=str),
                "created_at": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.warning(f"Could not store signals: {e}")

    async def _get_latest_icp(self) -> Optional[Dict[str, Any]]:
        """Get most recent ICP analysis"""
        try:
            result = self._get_sb().table(GTM_ICP_TABLE).select("analysis").order("created_at", desc=True).limit(1).execute()
            if result.data:
                return json.loads(result.data[0]["analysis"])
        except Exception:
            pass
        return None

    async def _get_latest_strategy(self) -> Optional[Dict[str, Any]]:
        """Get most recent strategy"""
        try:
            result = self._get_sb().table(GTM_STRATEGIES_TABLE).select("strategy").order("created_at", desc=True).limit(1).execute()
            if result.data:
                return json.loads(result.data[0]["strategy"])
        except Exception:
            pass
        return None

    async def _publish_signals(self, signals: Dict[str, Any]):
        """Publish signals to event bus for SAMA agents"""
        try:
            from shared.event_bus import event_bus
            if settings.LINKEDIN_AGENT_EVENT_BUS_ENABLED:
                for agent_name, agent_signals in signals.get("agents", {}).items():
                    target = f"sama_{agent_name.replace('_agent', '')}"
                    await event_bus.publish(
                        event_type="gtm_signal",
                        target_agent=target,
                        data=agent_signals
                    )
                # Also send LinkedIn signals to Growth Hub
                linkedin_signals = signals.get("agents", {}).get("linkedin_agent", {})
                if linkedin_signals:
                    await event_bus.publish(
                        event_type="gtm_signal",
                        target_agent="linkedin_agent",
                        data=linkedin_signals
                    )
                logger.info("📤 Signals published to event bus")
        except Exception as e:
            logger.warning(f"Event bus not available: {e}")


# Global instance
gtm_agent = GTMAgent()
