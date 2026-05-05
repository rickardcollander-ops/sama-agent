"""
Strategy Agent — cross-channel marketing strategist.

Pulls recent data from every enabled domain agent (SEO, Content, Ads,
Social, Reviews, Analytics, GEO) and synthesises a unified marketing
strategy: per-domain analysis, cross-channel priorities, and a roadmap.

The output is persisted to `marketing_strategies` so it can be displayed
in the dashboard and re-used by other agents as planning context.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)


DOMAINS = ["seo", "content", "ads", "social", "reviews", "analytics", "geo"]


class StrategyAgent:
    """Generates an overarching marketing strategy from per-agent activity."""

    def __init__(self, tenant_config=None):
        self.tenant_config = tenant_config
        api_key = (
            tenant_config.anthropic_api_key
            if tenant_config and getattr(tenant_config, "anthropic_api_key", None)
            else settings.ANTHROPIC_API_KEY
        )
        self.client = Anthropic(api_key=api_key) if api_key else None
        self.model = settings.CLAUDE_MODEL

    @property
    def tenant_id(self) -> str:
        return getattr(self.tenant_config, "tenant_id", "default") if self.tenant_config else "default"

    # ── Domain data gathering ────────────────────────────────────────────────

    async def _enabled_agents(self) -> List[str]:
        """Return the list of enabled domain agents for this tenant."""
        sb = get_supabase()
        try:
            res = (
                sb.table("tenant_agent_config")
                .select("agent_name,enabled")
                .eq("tenant_id", self.tenant_id)
                .execute()
            )
            rows = res.data or []
            enabled = {r["agent_name"] for r in rows if r.get("enabled")}
            # If no config rows yet, assume all domains are enabled.
            if not rows:
                return list(DOMAINS)
            return [d for d in DOMAINS if d in enabled]
        except Exception as e:
            logger.warning(f"[strategy] could not load tenant_agent_config: {e}")
            return list(DOMAINS)

    async def _gather_domain_snapshot(self, agent_name: str) -> Dict[str, Any]:
        """Pull a small snapshot of recent activity for *agent_name*."""
        sb = get_supabase()
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        snapshot: Dict[str, Any] = {"agent": agent_name}

        try:
            actions = (
                sb.table("agent_actions")
                .select("action_type,title,status,priority")
                .eq("agent_name", agent_name)
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            snapshot["recent_actions"] = actions.data or []
        except Exception:
            snapshot["recent_actions"] = []

        try:
            report = (
                sb.table("agent_reports")
                .select("summary,improvements,generated_at")
                .eq("agent_name", agent_name)
                .order("generated_at", desc=True)
                .limit(1)
                .execute()
            )
            snapshot["latest_report"] = (report.data or [None])[0]
        except Exception:
            snapshot["latest_report"] = None

        # Domain-specific extras
        try:
            if agent_name == "seo":
                kw = sb.table("seo_keywords").select("keyword,search_volume,position").limit(15).execute()
                snapshot["top_keywords"] = kw.data or []
            elif agent_name == "content":
                cp = sb.table("content_pieces").select("title,status,platform").limit(15).execute()
                snapshot["recent_content"] = cp.data or []
            elif agent_name == "geo":
                checks = (
                    sb.table("ai_visibility_checks")
                    .select("ai_engine,mentioned,sentiment")
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()
                )
                snapshot["recent_geo_checks"] = checks.data or []
        except Exception:
            pass

        return snapshot

    # ── Strategy synthesis ───────────────────────────────────────────────────

    async def generate_strategy(self, horizon: str = "quarterly") -> Dict[str, Any]:
        """Build a unified marketing strategy across all enabled domains."""
        if not self.client:
            return {"error": "ANTHROPIC_API_KEY not configured"}

        enabled = await self._enabled_agents()
        snapshots = await asyncio.gather(
            *(self._gather_domain_snapshot(d) for d in enabled)
        )
        domain_blob = json.dumps(snapshots, ensure_ascii=False, default=str)[:14000]

        brand_name = (
            getattr(self.tenant_config, "brand_name", None) if self.tenant_config else None
        ) or "the brand"

        system = (
            "You are the Chief Marketing Strategist for an autonomous marketing system. "
            "You synthesise raw activity from specialist agents into a single, coherent "
            "cross-channel marketing strategy. Be specific, prioritised and data-driven. "
            "No generic advice."
        )

        # Shapes here mirror the dashboard's TypeScript interface
        # (app/c/strategy/page.tsx) — domain_strategies and roadmap MUST be
        # arrays so the UI can iterate with .map(); object-keyed shapes break
        # the rendering completely.
        user_prompt = f"""Brand: {brand_name}
Planning horizon: {horizon}
Active marketing domains (only consider these): {', '.join(enabled)}

Per-domain activity & reports (last 30 days):
{domain_blob}

Produce a unified marketing strategy as JSON with EXACTLY these keys:

1. "headline" (string, max 18 words) — bold one-line summary of the plan.
2. "verdict" (string) — one of "critical", "weak", "improving", "strong".
3. "executive_summary" (string, 3-5 sentences) — what's working, what isn't, where to push.
4. "domain_strategies" (ARRAY, one entry per active domain in {enabled!r}):
     [{{"domain": "seo", "diagnosis": "...", "goal": "...", "key_actions": ["...","..."], "kpi": "..."}}, ...]
5. "cross_channel_priorities" (array of 3-5 objects) — initiatives spanning multiple domains:
     [{{"title": "...", "domains": ["seo","content"], "description": "...", "impact": "high|medium|low"}}, ...]
6. "roadmap" (ARRAY of exactly 3 milestone objects, in order 30d → 60d → 90d):
     [{{"horizon": "30d", "title": "...", "description": "...", "items": ["...","..."]}},
      {{"horizon": "60d", "title": "...", "description": "...", "items": ["...","..."]}},
      {{"horizon": "90d", "title": "...", "description": "...", "items": ["...","..."]}}]
7. "risks" (array of 2-4 strings) — what could derail the plan.
8. "north_star_metric" (object) — {{"name": "...", "target": "...", "current": "..."}}.

Respond ONLY with valid JSON (no markdown fences). Use ARRAYS for
domain_strategies and roadmap — never objects keyed by domain or horizon."""

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=3500,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )

        try:
            response = await asyncio.to_thread(_call)
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            strategy = json.loads(text.strip())
        except Exception as e:
            logger.error(f"[strategy] generation failed: {e}")
            return {"error": str(e)}

        saved = await self._save_strategy(strategy, enabled, horizon)
        return saved

    async def _save_strategy(
        self,
        strategy: Dict[str, Any],
        enabled: List[str],
        horizon: str,
    ) -> Dict[str, Any]:
        sb = get_supabase()
        record = {
            "tenant_id": self.tenant_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "headline": strategy.get("headline", "")[:500],
            "verdict": strategy.get("verdict", "improving"),
            "horizon": horizon,
            "strategy": strategy,
            "contributing_agents": enabled,
            "status": "active",
        }
        try:
            # Archive previous active strategy so only the latest is "active".
            sb.table("marketing_strategies").update({"status": "archived"}).eq(
                "tenant_id", self.tenant_id
            ).eq("status", "active").execute()
            ins = sb.table("marketing_strategies").insert(record).execute()
            if ins.data:
                return ins.data[0]
        except Exception as e:
            logger.warning(f"[strategy] could not persist strategy: {e}")
        return record

    async def get_current(self) -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        try:
            res = (
                sb.table("marketing_strategies")
                .select("*")
                .eq("tenant_id", self.tenant_id)
                .eq("status", "active")
                .order("generated_at", desc=True)
                .limit(1)
                .execute()
            )
            return (res.data or [None])[0]
        except Exception:
            return None

    async def list_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        sb = get_supabase()
        try:
            res = (
                sb.table("marketing_strategies")
                .select("id,generated_at,headline,verdict,horizon,status")
                .eq("tenant_id", self.tenant_id)
                .order("generated_at", desc=True)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    # ── Scheduler / fan-out entry point ──────────────────────────────────────

    async def run_cycle(self) -> str:
        result = await self.generate_strategy()
        if "error" in result:
            return f"strategy generation failed: {result['error']}"
        return f"strategy generated: {result.get('headline', '')[:80]}"


strategy_agent = StrategyAgent()
