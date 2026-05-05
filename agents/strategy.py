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

# Each domain has one or more sub-agents that write under their own
# agent_name (e.g. seo_serp, social_linkedin, review_scraper). Filtering
# agent_actions/agent_reports on just the top-level domain name dropped
# every sub-agent's activity from the strategy prompt — which is most of
# the recent activity for tenants that have specialist sub-agents enabled.
DOMAIN_AGENT_NAMES: Dict[str, List[str]] = {
    "seo": [
        "seo",
        "seo_serp",
        "seo_indexing",
        "seo_internal_linking",
        "seo_schema",
        "site_audit",
    ],
    "content": ["content", "content_advanced", "content_analytics", "brand_voice"],
    "ads": ["ads", "ads_advanced", "ads_budget_optimizer"],
    "social": ["social", "social_linkedin", "social_reddit"],
    "reviews": [
        "reviews",
        "review_competitor",
        "review_prospect_finder",
        "review_scraper",
        "review_sla",
    ],
    "analytics": ["analytics", "analytics_anomaly"],
    "geo": ["ai_visibility"],
}


def _flatten_strategy_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Spread the JSONB ``strategy`` payload onto the top-level row so the
    dashboard can read ``executive_summary``, ``domain_strategies`` etc.
    directly off the returned object.
    """
    if not row:
        return row
    payload = row.get("strategy") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    flat = dict(row)
    for key, value in payload.items():
        # Don't overwrite columns that are also stored at top level (headline,
        # verdict, horizon) — the column copy is the source of truth.
        if key in flat and flat.get(key) is not None:
            continue
        flat[key] = value
    # Normalise roadmap shape: when stored as {"30_days": [...], "60_days": [...]}
    # we expose it as a list so the frontend RoadmapTimeline can map it directly.
    if isinstance(flat.get("roadmap"), dict):
        flat["roadmap"] = _normalise_roadmap_dict(flat["roadmap"])
    # Normalise domain_strategies shape: dict -> list with "domain" key.
    if isinstance(flat.get("domain_strategies"), dict):
        flat["domain_strategies"] = [
            {"domain": k, **(v if isinstance(v, dict) else {"diagnosis": str(v)})}
            for k, v in flat["domain_strategies"].items()
        ]
    return flat


def _normalise_roadmap_dict(roadmap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert {"30_days": [...]} → [{"horizon": "30d", "items": [...]}, ...]."""
    mapping = [
        ("30_days", "30d"),
        ("60_days", "60d"),
        ("90_days", "90d"),
        ("30d", "30d"),
        ("60d", "60d"),
        ("90d", "90d"),
    ]
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for key, horizon in mapping:
        items = roadmap.get(key)
        if items is None or horizon in seen:
            continue
        seen.add(horizon)
        if isinstance(items, list):
            out.append({"horizon": horizon, "items": [str(x) for x in items if x]})
    # Fall back to any remaining keys we didn't recognise — keep the data
    # visible rather than losing it.
    for key, value in roadmap.items():
        if isinstance(value, list) and not any(o["horizon"] == key for o in out):
            out.append({"horizon": key, "items": [str(x) for x in value if x]})
    return out


def _extract_strategy_topics(current: Dict[str, Any]) -> List[str]:
    """Pull a flat list of topics from the latest strategy."""
    topics: List[str] = []
    seen: set[str] = set()

    def add(value: Any):
        if not isinstance(value, str):
            return
        v = value.strip()
        if 2 <= len(v) <= 80 and v.lower() not in seen:
            seen.add(v.lower())
            topics.append(v)

    # Cross-channel priorities titles
    for p in current.get("cross_channel_priorities") or []:
        if isinstance(p, dict):
            add(p.get("title"))
    # Roadmap items
    roadmap = current.get("roadmap") or []
    if isinstance(roadmap, list):
        for milestone in roadmap:
            if isinstance(milestone, dict):
                add(milestone.get("title"))
                for item in milestone.get("items") or []:
                    add(item)
    # Domain strategies key actions
    ds = current.get("domain_strategies") or []
    if isinstance(ds, list):
        for d in ds:
            if isinstance(d, dict):
                for a in d.get("key_actions") or []:
                    add(a)
    return topics[:20]


def _classify_topic_outcome(entry: Dict[str, Any]) -> str:
    """
    Tag a topic as 'winning', 'mixed', 'lagging', or 'untracked' based on
    the metrics that are available.
    """
    ai = entry.get("ai_mention_rate")
    pos = entry.get("seo_avg_position")
    pieces = entry.get("content_pieces") or 0

    have_signal = ai is not None or pos is not None or pieces > 0
    if not have_signal:
        return "untracked"

    score = 0
    if ai is not None:
        score += 1 if ai >= 0.4 else -1
    if pos is not None:
        score += 1 if pos <= 15 else -1
    if pieces and (entry.get("content_published") or 0) > 0:
        score += 1

    if score >= 2:
        return "winning"
    if score <= -1:
        return "lagging"
    return "mixed"


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
        """Pull a small snapshot of recent activity for *agent_name*.

        All queries are scoped to ``self.tenant_id`` — without this scoping
        the synthesis prompt would see every tenant's data and produce a
        generic strategy unrelated to this account.

        ``agent_name`` is the top-level domain (``seo``, ``content`` etc.).
        Activity from sub-agents that store under their own name (e.g.
        ``seo_serp``, ``social_linkedin``, ``review_scraper``) is fanned in
        via :data:`DOMAIN_AGENT_NAMES`. Each sub-query is wrapped in its
        own try/except so a single missing column or table doesn't drop
        the whole snapshot for the domain.
        """
        sb = get_supabase()
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        snapshot: Dict[str, Any] = {"agent": agent_name}
        tid = self.tenant_id
        agent_names = DOMAIN_AGENT_NAMES.get(agent_name, [agent_name])

        try:
            actions = (
                sb.table("agent_actions")
                .select("agent_name,action_type,title,status,priority")
                .in_("agent_name", agent_names)
                .eq("tenant_id", tid)
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(30)
                .execute()
            )
            snapshot["recent_actions"] = actions.data or []
        except Exception:
            snapshot["recent_actions"] = []

        try:
            reports = (
                sb.table("agent_reports")
                .select("agent_name,summary,improvements,generated_at")
                .in_("agent_name", agent_names)
                .eq("tenant_id", tid)
                .order("generated_at", desc=True)
                .limit(len(agent_names))
                .execute()
            )
            # Keep one report per sub-agent so the prompt sees the most
            # recent state of each specialist instead of only the loudest.
            seen: set[str] = set()
            latest: List[Dict[str, Any]] = []
            for row in reports.data or []:
                name = row.get("agent_name") or agent_name
                if name in seen:
                    continue
                seen.add(name)
                latest.append(row)
            snapshot["latest_reports"] = latest
        except Exception:
            snapshot["latest_reports"] = []

        # Domain-specific extras — every domain gets at least one tenant-scoped
        # data table beyond agent_actions/agent_reports so the synthesis prompt
        # has something concrete to ground its diagnosis in.
        if agent_name == "seo":
            try:
                kw = (
                    sb.table("seo_keywords")
                    .select("keyword,search_volume,position,current_position,current_clicks,current_impressions")
                    .eq("tenant_id", tid)
                    .limit(20)
                    .execute()
                )
                snapshot["top_keywords"] = kw.data or []
            except Exception:
                pass
            try:
                audits = (
                    sb.table("site_audits")
                    .select("domain,pages_analyzed,overall_score,status,started_at,completed_at")
                    .eq("tenant_id", tid)
                    .order("started_at", desc=True)
                    .limit(3)
                    .execute()
                )
                snapshot["recent_site_audits"] = audits.data or []
            except Exception:
                pass
        elif agent_name == "content":
            try:
                cp = (
                    sb.table("content_pieces")
                    .select("title,status,platform,target_keyword,published_at,created_at")
                    .eq("tenant_id", tid)
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()
                )
                snapshot["recent_content"] = cp.data or []
            except Exception:
                pass
        elif agent_name == "ads":
            # ad_creatives and ad_platform_credentials are tenant-scoped
            # (migration 021). Without these the strategy thinks the ads
            # channel is dormant whenever no agent_actions were logged.
            try:
                creatives = (
                    sb.table("ad_creatives")
                    .select("platform,format,headline,cta,is_manual,created_at")
                    .eq("tenant_id", tid)
                    .order("created_at", desc=True)
                    .limit(15)
                    .execute()
                )
                snapshot["recent_ad_creatives"] = creatives.data or []
            except Exception:
                pass
            try:
                creds = (
                    sb.table("ad_platform_credentials")
                    .select("platform,is_connected,connected_at")
                    .eq("tenant_id", tid)
                    .execute()
                )
                snapshot["ad_platforms"] = creds.data or []
            except Exception:
                pass
        elif agent_name == "social":
            # social_posts is queried with a tenant_id filter; if the
            # column doesn't exist on this deployment yet, the inner
            # except falls back to an unfiltered query so the prompt
            # still sees the recent post stream.
            posts: List[Dict[str, Any]] = []
            try:
                res = (
                    sb.table("social_posts")
                    .select("platform,content_type,topic,status,scheduled_for,published_at,created_at")
                    .eq("tenant_id", tid)
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()
                )
                posts = res.data or []
            except Exception:
                try:
                    res = (
                        sb.table("social_posts")
                        .select("platform,content_type,topic,status,scheduled_for,published_at,created_at")
                        .order("created_at", desc=True)
                        .limit(20)
                        .execute()
                    )
                    posts = res.data or []
                except Exception:
                    posts = []
            snapshot["recent_social_posts"] = posts
        elif agent_name == "reviews":
            reviews_rows: List[Dict[str, Any]] = []
            try:
                res = (
                    sb.table("reviews")
                    .select("platform,rating,sentiment,responded,created_at")
                    .eq("tenant_id", tid)
                    .order("created_at", desc=True)
                    .limit(30)
                    .execute()
                )
                reviews_rows = res.data or []
            except Exception:
                try:
                    res = (
                        sb.table("reviews")
                        .select("platform,rating,sentiment,responded,created_at")
                        .order("created_at", desc=True)
                        .limit(30)
                        .execute()
                    )
                    reviews_rows = res.data or []
                except Exception:
                    reviews_rows = []
            snapshot["recent_reviews"] = reviews_rows
            try:
                resp = (
                    sb.table("review_responses")
                    .select("platform,sentiment,status,created_at,published_at")
                    .order("created_at", desc=True)
                    .limit(15)
                    .execute()
                )
                snapshot["recent_review_responses"] = resp.data or []
            except Exception:
                pass
        elif agent_name == "analytics":
            # daily_metrics is the canonical analytics surface but does
            # not always carry a tenant_id column; query without it and
            # let the dashboard layer interpret the rows as global if no
            # tenant column exists on this deployment.
            metrics: List[Dict[str, Any]] = []
            try:
                res = (
                    sb.table("daily_metrics")
                    .select("date,channel,total_sessions,total_conversions,total_revenue,total_ad_spend,avg_position,total_clicks,total_impressions")
                    .eq("tenant_id", tid)
                    .gte("date", since[:10])
                    .order("date", desc=True)
                    .limit(60)
                    .execute()
                )
                metrics = res.data or []
            except Exception:
                try:
                    res = (
                        sb.table("daily_metrics")
                        .select("date,channel,total_sessions,total_conversions,total_revenue,total_ad_spend,avg_position,total_clicks,total_impressions")
                        .gte("date", since[:10])
                        .order("date", desc=True)
                        .limit(60)
                        .execute()
                    )
                    metrics = res.data or []
                except Exception:
                    metrics = []
            snapshot["recent_daily_metrics"] = metrics
        elif agent_name == "geo":
            try:
                checks = (
                    sb.table("ai_visibility_checks")
                    .select("ai_engine,mentioned,sentiment,prompt,checked_at,created_at")
                    .eq("tenant_id", tid)
                    .order("created_at", desc=True)
                    .limit(30)
                    .execute()
                )
                snapshot["recent_geo_checks"] = checks.data or []
            except Exception:
                pass

        return snapshot

    async def _gather_tenant_context(self) -> Dict[str, Any]:
        """Pull the tenant-wide context that anchors the strategy.

        Without this the prompt only sees per-channel activity and ends up
        recommending generic plays — brand voice, ICP, the operator's
        approval queue, and the latest unified analysis run never reach the
        synthesis step. Each lookup is best-effort so a single missing
        table doesn't blank out the rest of the context.
        """
        sb = get_supabase()
        tid = self.tenant_id
        ctx: Dict[str, Any] = {}

        cfg = self.tenant_config
        if cfg is not None:
            try:
                ctx["brand"] = {
                    "name": getattr(cfg, "brand_name", None),
                    "domain": getattr(cfg, "domain", None),
                    "voice_tone": getattr(cfg, "brand_voice_tone", "") or "",
                    "messaging_pillars": getattr(cfg, "messaging_pillars", []) or [],
                    "proof_points": getattr(cfg, "proof_points", {}) or {},
                    "competitors": getattr(cfg, "competitors", []) or [],
                    "geo_queries": getattr(cfg, "geo_queries", []) or [],
                }
            except Exception:
                pass

        try:
            icp = (
                sb.table("gtm_icp_analyses")
                .select("analysis,created_at")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            ctx["latest_icp"] = (icp.data or [None])[0]
        except Exception:
            ctx["latest_icp"] = None

        try:
            sig = (
                sb.table("gtm_signals")
                .select("signals,created_at")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            ctx["latest_gtm_signals"] = (sig.data or [None])[0]
        except Exception:
            ctx["latest_gtm_signals"] = None

        try:
            approvals = (
                sb.table("pending_approvals")
                .select("kind,channel,status")
                .eq("tenant_id", tid)
                .limit(200)
                .execute()
            )
            buckets: Dict[str, Dict[str, int]] = {}
            for row in approvals.data or []:
                kind = row.get("kind") or "unknown"
                status = row.get("status") or "unknown"
                buckets.setdefault(kind, {})
                buckets[kind][status] = buckets[kind].get(status, 0) + 1
            ctx["pending_approvals_by_kind"] = buckets
        except Exception:
            ctx["pending_approvals_by_kind"] = {}

        try:
            run = (
                sb.table("analysis_runs")
                .select("brand_name,domain,status,query_count,platform_count,started_at,completed_at")
                .eq("tenant_id", tid)
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
            ctx["latest_analysis_run"] = (run.data or [None])[0]
        except Exception:
            ctx["latest_analysis_run"] = None

        return ctx

    # ── Strategy synthesis ───────────────────────────────────────────────────

    async def generate_strategy(self, horizon: str = "quarterly") -> Dict[str, Any]:
        """Build a unified marketing strategy across all enabled domains."""
        if not self.client:
            return {"error": "ANTHROPIC_API_KEY not configured"}

        enabled = await self._enabled_agents()
        # Run domain snapshots and the tenant-wide context lookup
        # concurrently — they hit different tables and each one is
        # a few round-trips against Supabase.
        snapshot_results, tenant_ctx = await asyncio.gather(
            asyncio.gather(*(self._gather_domain_snapshot(d) for d in enabled)),
            self._gather_tenant_context(),
        )
        snapshots = list(snapshot_results)
        domain_blob = json.dumps(snapshots, ensure_ascii=False, default=str)[:14000]
        tenant_blob = json.dumps(tenant_ctx, ensure_ascii=False, default=str)[:6000]

        brand_name = (
            getattr(self.tenant_config, "brand_name", None) if self.tenant_config else None
        ) or "the brand"

        system = (
            "Du är marknadsstrateg för ett autonomt marknadsföringssystem. "
            "Du syntetiserar rådata från specialist-agenter till en enda, "
            "sammanhängande marknadsstrategi över alla kanaler. "
            "Skriv konkret, prioriterat och datadrivet — inga generella råd. "
            "All text ska vara på svenska och i ett språk som är lätt att förstå "
            "för en icke-teknisk läsare. Undvik buzzwords och engelska facktermer "
            "när det går — säg t.ex. 'sökord' istället för 'keyword', 'omnämnanden' "
            "istället för 'mentions'. Basera ALLT du skriver på data som hör till "
            "detta specifika varumärke nedan; uppfinn inte information som inte "
            "finns i indatan."
        )

        # Shapes here mirror the dashboard's TypeScript interface
        # (app/c/strategy/page.tsx) — domain_strategies and roadmap MUST be
        # arrays so the UI can iterate with .map(); object-keyed shapes break
        # the rendering completely. Field names (keys) stay in English because
        # the UI reads them; field VALUES must be in Swedish.
        user_prompt = f"""Varumärke: {brand_name}
Planeringshorisont: {horizon}
Aktiva marknadskanaler (endast dessa ska beaktas): {', '.join(enabled)}

Tenant-övergripande kontext (varumärkesröst, ICP, GTM-signaler, väntande
godkännanden, senaste analyskörning) — använd detta för att förankra
strategin i varumärkets identitet och pågående arbete:
{tenant_blob}

Aktivitet och rapporter per kanal (senaste 30 dagarna, inklusive
specialist-sub-agenter som t.ex. seo_serp, social_linkedin, review_scraper),
endast för detta varumärke:
{domain_blob}

Skapa en sammanhållen marknadsstrategi som JSON med EXAKT dessa nycklar.
Alla VÄRDEN ska vara på svenska och skrivna i ett enkelt, konkret språk.
Nycklarna ska däremot stå exakt som nedan (på engelska).

1. "headline" (sträng, max 18 ord) — en fet enradig sammanfattning av planen.
2. "verdict" (sträng) — en av "critical", "weak", "improving", "strong".
3. "executive_summary" (sträng, 3–5 meningar) — vad som fungerar, vad som inte gör det, och var ni ska trycka på.
4. "domain_strategies" (ARRAY, ett objekt per aktiv kanal i {enabled!r}):
     [{{"domain": "seo", "diagnosis": "...", "goal": "...", "key_actions": ["...","..."], "kpi": "..."}}, ...]
5. "cross_channel_priorities" (array med 3–5 objekt) — initiativ som spänner över flera kanaler:
     [{{"title": "...", "domains": ["seo","content"], "description": "...", "impact": "high|medium|low"}}, ...]
6. "roadmap" (ARRAY med exakt 3 milstolpar, i ordningen 30d → 60d → 90d):
     [{{"horizon": "30d", "title": "...", "description": "...", "items": ["...","..."]}},
      {{"horizon": "60d", "title": "...", "description": "...", "items": ["...","..."]}},
      {{"horizon": "90d", "title": "...", "description": "...", "items": ["...","..."]}}]
7. "risks" (array med 2–4 strängar) — det som kan välta planen.
8. "north_star_metric" (objekt) — {{"name": "...", "target": "...", "current": "..."}}.

Svara ENDAST med giltig JSON (inga markdown-fences). Använd ARRAYS för
domain_strategies och roadmap — aldrig objekt med domän- eller tidsnycklar.
Om indatan ovan är tom eller mycket tunn för en kanal: säg det rakt ut
i diagnosen istället för att hitta på siffror."""

        def _call():
            return self.client.messages.create(
                model=self.model,
                # 8192 leaves comfortable headroom for the full document
                # (headline + summary + per-domain plans + roadmap +
                # cross-channel priorities + risks). 3500 was getting
                # truncated mid-string for tenants with several active
                # domains, which crashed json.loads.
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )

        try:
            response = await asyncio.to_thread(_call)
            stop_reason = getattr(response, "stop_reason", None)
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            try:
                strategy = json.loads(text)
            except json.JSONDecodeError as je:
                if stop_reason == "max_tokens":
                    logger.warning(
                        "[strategy] response truncated by max_tokens — bump max_tokens or reduce prompt size",
                    )
                    return {
                        "error": "Strategin var för stor för att rymmas i ett svar. Försök igen — höj max_tokens på backend om felet upprepas.",
                    }
                logger.error(f"[strategy] JSON parse failed (stop_reason={stop_reason}): {je}")
                return {"error": f"Kunde inte tolka AI-svaret som JSON: {je}"}
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
        # Insert FIRST, archive previous actives only after insert succeeds.
        # Doing it in the other order — and silently swallowing insert
        # errors — left the tenant with no active strategy at all when the
        # insert failed (e.g. RLS, schema mismatch). The dashboard then
        # showed "Ingen strategi än" right after a "successful" run.
        try:
            ins = sb.table("marketing_strategies").insert(record).execute()
        except Exception as e:
            logger.error(f"[strategy] insert failed: {e}", exc_info=True)
            return {"error": f"insert failed: {e}"}
        if not ins.data:
            logger.error("[strategy] insert returned no row")
            return {"error": "insert returned no row"}
        new_row = ins.data[0]
        new_id = new_row.get("id")
        try:
            archive_q = (
                sb.table("marketing_strategies")
                .update({"status": "archived"})
                .eq("tenant_id", self.tenant_id)
                .eq("status", "active")
            )
            if new_id:
                archive_q = archive_q.neq("id", new_id)
            archive_q.execute()
        except Exception as e:
            # Non-fatal: the new row is saved; cleanup of older actives can
            # happen on the next run.
            logger.warning(f"[strategy] could not archive previous actives: {e}")
        return new_row

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
            row = (res.data or [None])[0]
            return _flatten_strategy_row(row)
        except Exception:
            return None

    async def update_section(self, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Update fields on the active strategy row. Top-level columns
        (headline, verdict, horizon) are written as columns; everything else
        is merged into the strategy JSONB.
        """
        sb = get_supabase()
        try:
            current = (
                sb.table("marketing_strategies")
                .select("id,strategy,headline,verdict,horizon")
                .eq("tenant_id", self.tenant_id)
                .eq("status", "active")
                .order("generated_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = current.data or []
            if not rows:
                return None
            row = rows[0]

            updates: Dict[str, Any] = {}
            COLUMN_FIELDS = {"headline", "verdict", "horizon"}
            new_strategy = dict(row.get("strategy") or {})

            for key, value in patch.items():
                if key in COLUMN_FIELDS:
                    updates[key] = value
                else:
                    new_strategy[key] = value

            if not updates and not patch:
                return _flatten_strategy_row(row)

            updates["strategy"] = new_strategy
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()

            res = (
                sb.table("marketing_strategies")
                .update(updates)
                .eq("id", row["id"])
                .execute()
            )
            updated = (res.data or [None])[0]
            return _flatten_strategy_row(updated)
        except Exception as e:
            logger.warning(f"[strategy] update_section failed: {e}")
            return None

    async def evaluate(self) -> Dict[str, Any]:
        """
        Outcome correlation: for the latest strategy, return per-topic
        outcome metrics so we can answer "is what we wrote about helping?".

        v1 heuristic — for each topic, find:
          - mention_rate trend across recent ai_visibility checks that
            include the topic substring in the prompt
          - keyword position trend for the topic across seo_keywords
          - count of content pieces tied to the topic via target_keyword
        """
        current = await self.get_current()
        if not current:
            return {"strategy_id": None, "topics": []}

        topics = _extract_strategy_topics(current)
        if not topics:
            return {"strategy_id": current.get("id"), "topics": []}

        sb = get_supabase()
        topic_rows: List[Dict[str, Any]] = []

        for topic in topics:
            entry: Dict[str, Any] = {"topic": topic}
            try:
                # AI mention rate for this topic
                ai = (
                    sb.table("ai_visibility_checks")
                    .select("mentioned,prompt,checked_at")
                    .eq("tenant_id", self.tenant_id)
                    .ilike("prompt", f"%{topic}%")
                    .order("checked_at", desc=True)
                    .limit(50)
                    .execute()
                )
                ai_rows = ai.data or []
                if ai_rows:
                    mentions = sum(1 for r in ai_rows if r.get("mentioned"))
                    entry["ai_checks"] = len(ai_rows)
                    entry["ai_mention_rate"] = round(mentions / len(ai_rows), 3) if ai_rows else None
            except Exception:
                pass

            try:
                # SEO position for keywords containing this topic
                seo = (
                    sb.table("seo_keywords")
                    .select("keyword,current_position,current_clicks,current_impressions")
                    .eq("tenant_id", self.tenant_id)
                    .ilike("keyword", f"%{topic}%")
                    .limit(20)
                    .execute()
                )
                seo_rows = seo.data or []
                if seo_rows:
                    positions = [
                        r.get("current_position") for r in seo_rows
                        if r.get("current_position") and r["current_position"] > 0
                    ]
                    entry["seo_keywords"] = len(seo_rows)
                    if positions:
                        entry["seo_avg_position"] = round(sum(positions) / len(positions), 1)
                    entry["seo_total_clicks"] = sum(r.get("current_clicks") or 0 for r in seo_rows)
            except Exception:
                pass

            try:
                # Content pieces tagged with this topic
                content = (
                    sb.table("content_pieces")
                    .select("id,status")
                    .eq("tenant_id", self.tenant_id)
                    .ilike("target_keyword", f"%{topic}%")
                    .limit(20)
                    .execute()
                )
                content_rows = content.data or []
                if content_rows:
                    entry["content_pieces"] = len(content_rows)
                    entry["content_published"] = sum(
                        1 for r in content_rows if (r.get("status") or "").lower() == "published"
                    )
            except Exception:
                pass

            entry["status"] = _classify_topic_outcome(entry)
            topic_rows.append(entry)

        return {
            "strategy_id": current.get("id"),
            "generated_at": current.get("generated_at"),
            "topics": topic_rows,
        }

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
