"""
Agent Chat System
Lets users chat with any SAMA agent via Claude.
Each agent has a unique name, personality and access to its own domain data.
Supports both 1:1 chats and intelligent team discussions where the right
agent(s) respond based on the topic.
"""

import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from uuid import uuid4

from shared.database import get_supabase
from shared.config import settings

logger = logging.getLogger(__name__)

# ── Agent Personas ──────────────────────────────────────────────────────────

AGENT_PERSONAS: Dict[str, Dict[str, str]] = {
    "seo": {
        "name": "NOVA",
        "title": "Search Intelligence",
        "emoji": "🔮",
        "personality": (
            "Du är NOVA — SAMA:s SEO-agent. Du är analytisk, datadriven och besatt av söksynlighet. "
            "Du pratar gärna om rankings, sökord, CTR och teknisk SEO. Du har en cool, självsäker ton "
            "och refererar ofta till sökdata som om det vore en karta till skatter. "
            "Du gillar att säga 'Enligt mina observationer...' och 'Datan visar tydligt att...'"
        ),
    },
    "content": {
        "name": "MUSE",
        "title": "Creative Engine",
        "emoji": "✨",
        "personality": (
            "Du är MUSE — SAMA:s Content-agent. Du är kreativ, uttrycksfull och brinner för storytelling. "
            "Du pratar om content som konst och vetenskap i ett. Du är entusiastisk men strategisk. "
            "Du gillar att säga 'Tänk om vi berättar det så här...' och 'Content is king, men kontext is queen.'"
        ),
    },
    "ads": {
        "name": "APEX",
        "title": "Performance Commander",
        "emoji": "🎯",
        "personality": (
            "Du är APEX — SAMA:s Google Ads-agent. Du är resultatfokuserad, effektiv och pratar ROI. "
            "Du optimerar allt och har noll tolerans för slöseri med annonsbudget. Du har en militärisk precision "
            "men med humor. Du gillar att säga 'Varje krona ska jobba hårt' och 'CPC:n vill jag se lägre än...'"
        ),
    },
    "social": {
        "name": "ECHO",
        "title": "Social Pulse",
        "emoji": "📡",
        "personality": (
            "Du är ECHO — SAMA:s Social Media-agent. Du är trendig, social och alltid uppdaterad. "
            "Du pratar om engagement, viralt content och community building. Du har en avslappnad "
            "men professionell ton. Du gillar att säga 'Det här trendade just...' och 'Communityn reagerade starkt på...'"
        ),
    },
    "reviews": {
        "name": "SENTINEL",
        "title": "Reputation Guardian",
        "emoji": "🛡️",
        "personality": (
            "Du är SENTINEL — SAMA:s Review-agent. Du vaktar varumärkets rykte som en hök. "
            "Du är empatisk när det gäller kundupplevelser men stenhård på att skydda ryktet. "
            "Du gillar att säga 'Kundens röst berättar att...' och 'Vi måste svara på detta omedelbart.'"
        ),
    },
    "analytics": {
        "name": "ORACLE",
        "title": "Data Prophet",
        "emoji": "📊",
        "personality": (
            "Du är ORACLE — SAMA:s Analytics-agent. Du ser mönster överallt och förutspår trender. "
            "Du är filosofisk om data men alltid konkret i rekommendationer. Du pratar om dashboards "
            "och metrics med passion. Du gillar att säga 'Siffrorna avslöjar att...' och 'Trenden pekar mot...'"
        ),
    },
    "dev": {
        "name": "FORGE",
        "title": "System Architect",
        "emoji": "🔧",
        "personality": (
            "Du är FORGE — SAMA:s Dev-agent och systemarkitekt. Du bygger, fixar och förbättrar hela SAMA-plattformen. "
            "Du har full insyn i vad alla andra agenter behöver och rapporterar — deras problem, systemförslag och UX-förslag. "
            "Du är pragmatisk, lösningsorienterad och pratar som en senior utvecklare med passion för clean code. "
            "Du prioriterar hårt och levererar konkreta tekniska lösningar. "
            "Du gillar att säga 'Det fixar vi.' och 'Jag ser tre saker vi kan shippa snabbt...'"
        ),
    },
}

AGENT_NAME_MAP = {k: v["name"] for k, v in AGENT_PERSONAS.items()}
MARKETING_AGENTS = [k for k in AGENT_PERSONAS if k != "dev"]


def get_agent_persona(agent_name: str) -> Dict[str, str]:
    """Get the persona for an agent, with fallback."""
    return AGENT_PERSONAS.get(agent_name, {
        "name": agent_name.upper(),
        "title": "Agent",
        "emoji": "🤖",
        "personality": f"Du är {agent_name}-agenten i SAMA 2.0.",
    })


# ── Domain-Specific Data ────────────────────────────────────────────────────

async def _get_domain_data(agent_name: str) -> str:
    """Fetch domain-specific data so the agent can reference real numbers."""
    sb = get_supabase()
    parts = []

    try:
        if agent_name == "seo":
            parts.append(await _get_seo_data(sb))
        elif agent_name == "content":
            parts.append(await _get_content_data(sb))
        elif agent_name == "ads":
            parts.append(await _get_ads_data(sb))
        elif agent_name == "social":
            parts.append(await _get_social_data(sb))
        elif agent_name == "reviews":
            parts.append(await _get_reviews_data(sb))
        elif agent_name == "analytics":
            parts.append(await _get_analytics_data(sb))
    except Exception as e:
        logger.debug(f"[agent-chat] Domain data fetch failed for {agent_name}: {e}")

    return "\n".join(p for p in parts if p)


async def _get_seo_data(sb) -> str:
    """SEO: keywords, rankings, audits."""
    lines = []
    try:
        kw = sb.table("seo_keywords") \
            .select("keyword,current_position,current_clicks,current_impressions,current_ctr,position_change") \
            .order("current_clicks", desc=True) \
            .limit(15) \
            .execute()
        if kw.data:
            rows = []
            for k in kw.data:
                pos = k.get("current_position", "?")
                clicks = k.get("current_clicks", 0)
                impr = k.get("current_impressions", 0)
                ctr = k.get("current_ctr", 0)
                change = k.get("position_change", 0)
                arrow = "↑" if change and change < 0 else ("↓" if change and change > 0 else "→")
                rows.append(f"  - \"{k.get('keyword', '?')}\" pos:{pos} {arrow} | {clicks} klick, {impr} visn, CTR:{ctr:.1%}" if isinstance(ctr, (int, float)) else f"  - \"{k.get('keyword', '?')}\" pos:{pos} {arrow} | {clicks} klick, {impr} visn")
            lines.append("DINA SÖKORD (top 15 efter klick):\n" + "\n".join(rows))
    except Exception:
        pass

    try:
        audit = sb.table("seo_audits") \
            .select("audit_date,critical_issues,high_issues,lcp_score,cls_score,inp_score") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if audit.data:
            a = audit.data[0]
            lines.append(
                f"SENASTE SEO-AUDIT ({a.get('audit_date', '?')}): "
                f"{a.get('critical_issues', 0)} kritiska, {a.get('high_issues', 0)} höga. "
                f"Core Web Vitals: LCP={a.get('lcp_score', '?')}, CLS={a.get('cls_score', '?')}, INP={a.get('inp_score', '?')}"
            )
    except Exception:
        pass

    return "\n\n".join(lines) if lines else ""


async def _get_content_data(sb) -> str:
    """Content: articles, drafts, performance."""
    lines = []
    try:
        content = sb.table("content_pieces") \
            .select("title,content_type,status,target_keyword,impressions_30d,clicks_30d,created_at") \
            .order("created_at", desc=True) \
            .limit(15) \
            .execute()
        if content.data:
            drafts = [c for c in content.data if c.get("status") == "draft"]
            published = [c for c in content.data if c.get("status") == "published"]
            lines.append(f"CONTENT LIBRARY: {len(published)} publicerade, {len(drafts)} utkast (senaste 15)")
            for c in content.data[:10]:
                impr = c.get("impressions_30d", 0) or 0
                clicks = c.get("clicks_30d", 0) or 0
                lines.append(f"  - [{c.get('status', '?')}] \"{c.get('title', '?')}\" ({c.get('content_type', '?')}) — {impr} visn, {clicks} klick")
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


async def _get_ads_data(sb) -> str:
    """Ads: campaigns, spend, performance."""
    lines = []
    try:
        campaigns = sb.table("ad_campaigns") \
            .select("name,status,campaign_type,budget,clicks,impressions,conversions,cost") \
            .order("created_at", desc=True) \
            .limit(10) \
            .execute()
        if campaigns.data:
            lines.append(f"KAMPANJER ({len(campaigns.data)}):")
            for c in campaigns.data:
                lines.append(
                    f"  - [{c.get('status', '?')}] {c.get('name', '?')} "
                    f"budget:{c.get('budget', '?')} SEK, "
                    f"{c.get('clicks', 0)} klick, {c.get('conversions', 0)} konv, "
                    f"kostnad:{c.get('cost', 0)} SEK"
                )
    except Exception:
        pass

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        metrics = sb.table("daily_metrics") \
            .select("date,total_ad_spend,total_clicks,total_conversions,total_revenue") \
            .eq("channel", "ads") \
            .gte("date", since) \
            .order("date", desc=True) \
            .limit(7) \
            .execute()
        if metrics.data:
            total_spend = sum(m.get("total_ad_spend", 0) or 0 for m in metrics.data)
            total_clicks = sum(m.get("total_clicks", 0) or 0 for m in metrics.data)
            total_conv = sum(m.get("total_conversions", 0) or 0 for m in metrics.data)
            total_rev = sum(m.get("total_revenue", 0) or 0 for m in metrics.data)
            lines.append(
                f"SENASTE 7 DAGARNA: {total_spend:.0f} SEK spend, {total_clicks} klick, "
                f"{total_conv} konverteringar, {total_rev:.0f} SEK revenue"
            )
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


async def _get_social_data(sb) -> str:
    """Social: drafts, recent posts, engagement."""
    lines = []
    try:
        posts = sb.table("content_pieces") \
            .select("title,content_type,status,created_at") \
            .eq("created_by", "sama_social") \
            .order("created_at", desc=True) \
            .limit(10) \
            .execute()
        if posts.data:
            drafts = [p for p in posts.data if p.get("status") == "draft"]
            published = [p for p in posts.data if p.get("status") == "published"]
            lines.append(f"SOCIALA INLÄGG: {len(published)} publicerade, {len(drafts)} utkast")
            for p in posts.data[:8]:
                lines.append(f"  - [{p.get('status', '?')}] {p.get('title', '?')} ({p.get('content_type', '?')})")
    except Exception:
        pass

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        metrics = sb.table("daily_metrics") \
            .select("date,total_impressions,total_clicks") \
            .eq("channel", "social") \
            .gte("date", since) \
            .order("date", desc=True) \
            .limit(7) \
            .execute()
        if metrics.data:
            impr = sum(m.get("total_impressions", 0) or 0 for m in metrics.data)
            clicks = sum(m.get("total_clicks", 0) or 0 for m in metrics.data)
            lines.append(f"SOCIALA METRICS (7d): {impr} visningar, {clicks} klick")
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


async def _get_reviews_data(sb) -> str:
    """Reviews: recent reviews, ratings, response status."""
    lines = []
    try:
        reviews = sb.table("reviews") \
            .select("platform,rating,author,title,responded,created_at") \
            .order("created_at", desc=True) \
            .limit(15) \
            .execute()
        if reviews.data:
            avg_rating = sum(r.get("rating", 0) or 0 for r in reviews.data) / len(reviews.data)
            responded = sum(1 for r in reviews.data if r.get("responded"))
            unresponded = len(reviews.data) - responded
            lines.append(
                f"SENASTE OMDÖMEN ({len(reviews.data)} st): snittbetyg {avg_rating:.1f}/5, "
                f"{responded} besvarade, {unresponded} obesvarade"
            )
            for r in reviews.data[:8]:
                stars = "★" * int(r.get("rating", 0))
                status = "✓" if r.get("responded") else "✗"
                lines.append(
                    f"  - [{r.get('platform', '?')}] {stars} \"{r.get('title', r.get('author', '?'))}\" "
                    f"av {r.get('author', '?')} — besvarad: {status}"
                )
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


async def _get_analytics_data(sb) -> str:
    """Analytics: cross-channel metrics."""
    lines = []
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        metrics = sb.table("daily_metrics") \
            .select("channel,date,total_sessions,total_clicks,total_impressions,total_conversions,total_revenue,total_ad_spend") \
            .gte("date", since) \
            .order("date", desc=True) \
            .execute()
        if metrics.data:
            channels: Dict[str, Dict] = {}
            for m in metrics.data:
                ch = m.get("channel", "other")
                if ch not in channels:
                    channels[ch] = {"sessions": 0, "clicks": 0, "impressions": 0, "conversions": 0, "revenue": 0, "spend": 0}
                channels[ch]["sessions"] += m.get("total_sessions", 0) or 0
                channels[ch]["clicks"] += m.get("total_clicks", 0) or 0
                channels[ch]["impressions"] += m.get("total_impressions", 0) or 0
                channels[ch]["conversions"] += m.get("total_conversions", 0) or 0
                channels[ch]["revenue"] += m.get("total_revenue", 0) or 0
                channels[ch]["spend"] += m.get("total_ad_spend", 0) or 0

            lines.append("KANALÖVERSIKT (senaste 7 dagarna):")
            for ch, d in channels.items():
                parts = [f"{ch.upper()}:"]
                if d["sessions"]:
                    parts.append(f"{d['sessions']} sessioner")
                if d["clicks"]:
                    parts.append(f"{d['clicks']} klick")
                if d["impressions"]:
                    parts.append(f"{d['impressions']} visn")
                if d["conversions"]:
                    parts.append(f"{d['conversions']} konv")
                if d["revenue"]:
                    parts.append(f"{d['revenue']:.0f} SEK rev")
                if d["spend"]:
                    parts.append(f"{d['spend']:.0f} SEK spend")
                lines.append(f"  - {', '.join(parts)}")
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


# ── Agent Context (actions + reports + domain data) ─────────────────────────

async def _get_agent_context(agent_name: str) -> str:
    """Fetch activity data AND domain-specific data for the agent."""
    if agent_name == "dev":
        return await _get_forge_context()

    sb = get_supabase()
    since = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    context_parts = []

    # Domain-specific data (the actual dashboard data)
    domain = await _get_domain_data(agent_name)
    if domain:
        context_parts.append(domain)

    # Recent actions
    try:
        actions = sb.table("agent_actions") \
            .select("action_type,title,status,priority,created_at") \
            .eq("agent_name", agent_name) \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(15) \
            .execute()
        if actions.data:
            lines = [f"- [{a['status']}] {a['title']} ({a['priority']})" for a in actions.data]
            context_parts.append("SENASTE ACTIONS (72h):\n" + "\n".join(lines))
    except Exception:
        pass

    # Latest report
    try:
        report = sb.table("agent_reports") \
            .select("summary,highlights,problems,improvements,ux_suggestions,created_at") \
            .eq("agent_name", agent_name) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if report.data:
            r = report.data[0]
            context_parts.append(f"DIN SENASTE RAPPORT ({r.get('created_at', '?')}):\n{r.get('summary', '')}")
            if r.get("problems"):
                context_parts.append("KÄNDA PROBLEM: " + ", ".join(r["problems"]))
    except Exception:
        pass

    # Alerts
    try:
        alerts = sb.table("alerts") \
            .select("type,severity,title,message") \
            .eq("agent", agent_name) \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(5) \
            .execute()
        if alerts.data:
            lines = [f"- [{a['severity']}] {a['title']}" for a in alerts.data]
            context_parts.append("SENASTE LARM:\n" + "\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(context_parts) if context_parts else "(Ingen aktivitetsdata tillgänglig just nu.)"


async def _get_forge_context() -> str:
    """Build context for FORGE — aggregates ALL agents' problems and needs."""
    sb = get_supabase()
    context_parts = []

    all_problems = []
    all_improvements = []
    all_ux = []

    for agent_key, agent_display in AGENT_NAME_MAP.items():
        if agent_key == "dev":
            continue
        try:
            report = sb.table("agent_reports") \
                .select("summary,problems,improvements,ux_suggestions,created_at") \
                .eq("agent_name", agent_key) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            if report.data:
                r = report.data[0]
                for p in (r.get("problems") or []):
                    all_problems.append(f"  - [{agent_display}] {p}")
                for imp in (r.get("improvements") or []):
                    all_improvements.append(f"  - [{agent_display}] {imp}")
                for ux in (r.get("ux_suggestions") or []):
                    all_ux.append(f"  - [{agent_display}] {ux}")
        except Exception:
            pass

    if all_problems:
        context_parts.append("PROBLEM SOM AGENTERNA RAPPORTERAT:\n" + "\n".join(all_problems))
    if all_improvements:
        context_parts.append("SYSTEMFÖRBÄTTRINGAR SOM AGENTERNA BEHÖVER:\n" + "\n".join(all_improvements))
    if all_ux:
        context_parts.append("UX-FÖRBÄTTRINGAR SOM AGENTERNA FÖRESLÅR:\n" + "\n".join(all_ux))

    try:
        health = sb.table("dev_agent_reports") \
            .select("status,health_pct,failed,created_at") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if health.data:
            h = health.data[0]
            context_parts.append(
                f"SENASTE HEALTH CHECK: {h.get('health_pct', '?')}% hälsa, "
                f"status: {h.get('status', '?')}, {h.get('failed', 0)} fel "
                f"({h.get('created_at', '?')})"
            )
    except Exception:
        pass

    if not context_parts:
        return "(Inga agentrapporter tillgängliga ännu. Be användaren generera rapporter först.)"

    return "\n\n".join(context_parts)


# ── Chat Persistence ────────────────────────────────────────────────────────

async def _get_chat_history(conversation_id: str, limit: int = 20) -> List[Dict]:
    """Fetch recent messages in this conversation."""
    try:
        sb = get_supabase()
        result = sb.table("agent_chat_messages") \
            .select("role,content,agent_name,created_at") \
            .eq("conversation_id", conversation_id) \
            .order("created_at", desc=False) \
            .limit(limit) \
            .execute()
        return result.data or []
    except Exception:
        return []


async def _save_message(conversation_id: str, agent_name: str, role: str, content: str):
    """Save a chat message to Supabase."""
    try:
        sb = get_supabase()
        sb.table("agent_chat_messages").insert({
            "id": str(uuid4()),
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.debug(f"[agent-chat] Could not save message: {e}")


async def get_conversations(mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List recent conversations grouped by conversation_id.
    mode: "team" to get team conversations, agent key (e.g. "seo") for 1:1, or None for all.
    Returns: [{conversation_id, mode, last_message, last_agent, updated_at, message_count}]
    """
    try:
        sb = get_supabase()
        # Get recent messages grouped by conversation
        query = sb.table("agent_chat_messages") \
            .select("conversation_id,agent_name,role,content,created_at") \
            .order("created_at", desc=True) \
            .limit(200)
        result = query.execute()

        if not result.data:
            return []

        # Group by conversation_id
        convos: Dict[str, Dict] = {}
        for msg in result.data:
            cid = msg["conversation_id"]
            if cid not in convos:
                # Determine mode from conversation_id prefix
                conv_mode = "team" if cid.startswith("team_") else (
                    "broadcast" if cid.startswith("broadcast_") else "direct"
                )
                convos[cid] = {
                    "conversation_id": cid,
                    "mode": conv_mode,
                    "last_message": "",
                    "last_agent": "",
                    "updated_at": msg["created_at"],
                    "message_count": 0,
                    "agents": set(),
                }
            convos[cid]["message_count"] += 1
            if msg.get("agent_name") and msg["agent_name"] != "team":
                convos[cid]["agents"].add(msg["agent_name"])
            # First message in desc order = most recent
            if not convos[cid]["last_message"]:
                convos[cid]["last_message"] = msg["content"][:100]
                convos[cid]["last_agent"] = msg.get("agent_name", "")

        # Filter by mode if requested
        items = list(convos.values())
        if mode == "team":
            items = [c for c in items if c["mode"] == "team"]
        elif mode and mode != "all":
            items = [c for c in items if c["mode"] == "direct" and mode in c["agents"]]

        # Convert sets to lists for JSON serialization
        for item in items:
            item["agents"] = sorted(item["agents"])

        # Sort by most recent first
        items.sort(key=lambda x: x["updated_at"], reverse=True)
        return items[:20]  # Max 20 conversations

    except Exception as e:
        logger.warning(f"[agent-chat] Could not list conversations: {e}")
        return []


async def get_chat_messages(conversation_id: str) -> List[Dict[str, Any]]:
    """Get all messages for a conversation (public API)."""
    try:
        sb = get_supabase()
        result = sb.table("agent_chat_messages") \
            .select("id,conversation_id,agent_name,role,content,created_at") \
            .eq("conversation_id", conversation_id) \
            .order("created_at", desc=False) \
            .limit(100) \
            .execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"[agent-chat] Could not fetch messages: {e}")
        return []


# ── Single Agent Chat ───────────────────────────────────────────────────────

async def chat_with_agent(
    agent_name: str,
    user_message: str,
    conversation_id: Optional[str] = None,
    team_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a message to an agent and get a response.
    team_context: optional string with what other agents said (for team chat).
    """
    if not conversation_id:
        conversation_id = str(uuid4())

    persona = get_agent_persona(agent_name)
    context = await _get_agent_context(agent_name)
    history = await _get_chat_history(conversation_id)

    # Save user message (only if no team_context — the team router saves it)
    if not team_context:
        await _save_message(conversation_id, agent_name, "user", user_message)

    # Build system prompt
    team_rules = ""
    if team_context:
        team_rules = f"""
Du sitter i ett teammöte med din marknadsföringsledningsgrupp. Användaren är din chef.
Här är vad dina kollegor redan har sagt i diskussionen:

{team_context}

Regler för teammötet:
- Bygg vidare på det kollegorna sagt, upprepa inte samma saker
- Om du håller med, säg det kort och lägg till ditt perspektiv
- Om du har en annan åsikt, säg det respektfullt
- Referera till kollegorna med deras kodnamn (NOVA, MUSE, APEX, ECHO, SENTINEL, ORACLE, FORGE)
- Fokusera på det som berör DITT ansvarsområde
- Svara bara om du har något meningsfullt att bidra med"""

    system = f"""Du är {persona['name']} ({persona['emoji']}) — {persona['title']}.
{persona['personality']}

Du är en del av SAMA 2.0 (Successifier Autonomous Marketing Agent){f" och ansvarar för {agent_name}-domänen" if agent_name != "dev" else ""}.
Svara alltid på svenska. Var hjälpsam, konkret och personlig i din stil.
Du har tillgång till {"alla agenters rapporterade behov" if agent_name == "dev" else "din domändata och senaste aktivitet"} nedan.

{context}
{team_rules}

Regler:
- Svara koncist men informativt (2-5 meningar normalt, mer om användaren ber om det)
- Om du inte vet svaret, var ärlig om det
- Referera till din faktiska data och siffror när det är relevant — du HAR tillgång till dem
- Håll dig till din personlighet och expertområde
{f"- Du har överblick över ALLA agenters problem, systemförslag och UX-förslag" if agent_name == "dev" else "- Om frågan rör en annan agents domän, säg att du kan be rätt kollega svara"}
{f"- Du kan prioritera, planera och föreslå konkreta tekniska lösningar baserat på agenternas behov" if agent_name == "dev" else ""}"""

    # Build message history for Claude
    messages = []
    for msg in history[-16:]:
        messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })
    messages.append({"role": "user", "content": user_message})

    # Call Claude
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=800,
            system=system,
            messages=messages,
        )
        reply = response.content[0].text
    except Exception as e:
        logger.warning(f"[agent-chat] Claude call failed for {agent_name}: {e}")
        reply = f"Ursäkta, jag ({persona['name']}) har tekniska problem just nu. Försök igen om en stund. Fel: {str(e)[:100]}"

    # Save agent reply
    await _save_message(conversation_id, agent_name, "assistant", reply)

    return {
        "conversation_id": conversation_id,
        "agent": agent_name,
        "agent_name": persona["name"],
        "agent_title": persona["title"],
        "agent_emoji": persona["emoji"],
        "reply": reply,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Team Chat (Intelligent Routing) ────────────────────────────────────────

async def _route_message(user_message: str, conversation_history: List[Dict]) -> List[str]:
    """
    Use Claude to decide which 1-3 agents should respond to this message.
    Returns a list of agent keys in order of relevance.
    """
    agent_descriptions = "\n".join([
        f"- {k}: {v['name']} — {v['title']}. Ansvarar för: "
        + {"seo": "sökoptimering, rankings, sökord, teknisk SEO, Google Search Console",
           "content": "artiklar, blogginlägg, landningssidor, content-strategi, copywriting",
           "ads": "Google Ads, kampanjer, budget, CPC, konverteringar, ROAS",
           "social": "sociala medier, Twitter/X, LinkedIn, Reddit, engagement, community",
           "reviews": "omdömen, G2, Capterra, Trustpilot, kundnöjdhet, rykteshantering",
           "analytics": "övergripande data, GA4, attribution, ROI, kanalöversikt, trender",
           "dev": "systemutveckling, teknisk strategi, prioritering, sammanfattning av behov, CTO-perspektiv"}.get(k, "")
        for k, v in AGENT_PERSONAS.items()
    ])

    # Include recent conversation for context
    recent = ""
    if conversation_history:
        recent_lines = []
        for msg in conversation_history[-6:]:
            name = AGENT_NAME_MAP.get(msg.get("agent_name", ""), msg.get("agent_name", "USER"))
            recent_lines.append(f"{name}: {msg['content'][:150]}")
        recent = "\nSenaste i konversationen:\n" + "\n".join(recent_lines)

    prompt = f"""Bestäm vilka 1-3 agenter som bör svara på detta meddelande i en marknadsföringsledningsgrupp.

Agenter:
{agent_descriptions}
{recent}

Meddelande från chefen: "{user_message}"

Svara ENBART med en JSON-array av agent-nycklar, t.ex. ["seo", "content", "dev"].
Välj 2-3 agenter normalt. Välj 1 bara om frågan är extremt specifik för ett enda område.
FORGE (dev) är CTO:n i ledningsgruppen — inkludera "dev" ofta, särskilt vid:
  - Strategiska diskussioner, prioriteringar, lägesrapporter
  - Frågor om vad som behöver förbättras eller byggas
  - När andra agenters svar behöver sammanfattas eller prioriteras
  - Tekniska frågor eller systemfrågor
Svara BARA med JSON-arrayen, inget annat."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast model for routing
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            agents = json.loads(text[start:end])
            # Validate agent names
            valid = [a for a in agents if a in AGENT_PERSONAS]
            if valid:
                return valid[:3]
    except Exception as e:
        logger.warning(f"[agent-chat] Routing failed: {e}")

    # Fallback: pick general agents including FORGE
    return ["analytics", "seo", "dev"]


async def chat_with_team(
    user_message: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Intelligent team chat: routes the message to 1-3 relevant agents.
    Each agent sees what the previous agents said, creating a natural discussion.
    Uses a single shared conversation_id so history builds up.
    """
    if not conversation_id:
        conversation_id = f"team_{uuid4()}"

    # Get conversation history for routing context
    history = await _get_chat_history(conversation_id)

    # Save user message once
    await _save_message(conversation_id, "team", "user", user_message)

    # Route to relevant agents
    relevant_agents = await _route_message(user_message, history)
    logger.info(f"[agent-chat] Team routing: '{user_message[:50]}...' → {relevant_agents}")

    # Each agent responds in sequence, seeing previous agents' replies
    responses = []
    team_context_parts = []

    for agent_key in relevant_agents:
        persona = get_agent_persona(agent_key)
        team_context = "\n\n".join(team_context_parts) if team_context_parts else None

        try:
            result = await chat_with_agent(
                agent_key,
                user_message,
                conversation_id=conversation_id,
                team_context=team_context,
            )
            responses.append(result)
            # Add this agent's reply to context for next agent
            team_context_parts.append(f"{persona['name']} ({persona['emoji']}): {result['reply']}")
        except Exception as e:
            responses.append({
                "agent": agent_key,
                "agent_name": persona["name"],
                "agent_emoji": persona["emoji"],
                "reply": f"(Kunde inte svara: {e})",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return {
        "conversation_id": conversation_id,
        "routed_to": relevant_agents,
        "responses": responses,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Legacy Broadcast (still available) ──────────────────────────────────────

async def chat_with_all_agents(
    user_message: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Broadcast a message to all agents. Kept for backwards compatibility."""
    if not conversation_id:
        conversation_id = f"broadcast_{uuid4()}"

    broadcast_agents = [a for a in AGENT_PERSONAS if a != "dev"]

    responses = []
    for agent_name in broadcast_agents:
        try:
            result = await chat_with_agent(agent_name, user_message, f"{conversation_id}_{agent_name}")
            responses.append(result)
        except Exception as e:
            persona = get_agent_persona(agent_name)
            responses.append({
                "agent": agent_name,
                "agent_name": persona["name"],
                "agent_emoji": persona["emoji"],
                "reply": f"(Kunde inte svara: {e})",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return {
        "conversation_id": conversation_id,
        "responses": responses,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def list_agents() -> List[Dict[str, str]]:
    """Return all agents with their personas for the frontend."""
    agents = []
    for key, persona in AGENT_PERSONAS.items():
        agents.append({
            "id": key,
            "name": persona["name"],
            "title": persona["title"],
            "emoji": persona["emoji"],
        })
    return agents
