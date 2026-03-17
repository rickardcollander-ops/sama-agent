"""
Agent Chat System
Lets users chat with any SAMA agent via Claude.
Each agent has a unique name, personality and access to its own data.
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


def get_agent_persona(agent_name: str) -> Dict[str, str]:
    """Get the persona for an agent, with fallback."""
    return AGENT_PERSONAS.get(agent_name, {
        "name": agent_name.upper(),
        "title": "Agent",
        "emoji": "🤖",
        "personality": f"Du är {agent_name}-agenten i SAMA 2.0.",
    })


async def _get_agent_context(agent_name: str) -> str:
    """Fetch recent activity data to give the agent context for the conversation."""
    # FORGE (dev agent) gets a completely different context — all agents' needs
    if agent_name == "dev":
        return await _get_forge_context()

    sb = get_supabase()
    since = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    context_parts = []

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
    """
    Build context for FORGE (dev agent) — aggregates ALL agents' problems,
    improvement suggestions, and UX suggestions so FORGE knows what to build.
    """
    sb = get_supabase()
    context_parts = []

    agent_names_map = {
        "seo": "NOVA", "content": "MUSE", "ads": "APEX",
        "social": "ECHO", "reviews": "SENTINEL", "analytics": "ORACLE",
    }

    # Collect latest report from every agent
    all_problems = []
    all_improvements = []
    all_ux = []

    for agent_key, agent_display in agent_names_map.items():
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

    # Dev agent health check
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


async def chat_with_agent(
    agent_name: str,
    user_message: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a message to an agent and get a response.
    The agent has access to its own activity data and maintains conversation history.
    """
    if not conversation_id:
        conversation_id = str(uuid4())

    persona = get_agent_persona(agent_name)
    context = await _get_agent_context(agent_name)
    history = await _get_chat_history(conversation_id)

    # Save user message
    await _save_message(conversation_id, agent_name, "user", user_message)

    # Build system prompt
    system = f"""Du är {persona['name']} ({persona['emoji']}) — {persona['title']}.
{persona['personality']}

Du är en del av SAMA 2.0 (Successifier Autonomous Marketing Agent){f" och ansvarar för {agent_name}-domänen" if agent_name != "dev" else ""}.
Svara alltid på svenska. Var hjälpsam, konkret och personlig i din stil.
Du har tillgång till {"alla agenters rapporterade behov" if agent_name == "dev" else "din senaste aktivitetsdata"} nedan.

{context}

Regler:
- Svara koncist men informativt (2-5 meningar normalt, mer om användaren ber om det)
- Om du inte vet svaret, var ärlig om det
- Referera till din faktiska data när det är relevant
- Håll dig till din personlighet och expertområde
{f"- Du har överblick över ALLA agenters problem, systemförslag och UX-förslag" if agent_name == "dev" else "- Om frågan rör en annan agents domän, säg att du kan skicka frågan vidare till rätt kollega"}
{f"- Du kan prioritera, planera och föreslå konkreta tekniska lösningar baserat på agenternas behov" if agent_name == "dev" else ""}
{f"- Referera till agenterna med deras kodnamn (NOVA, MUSE, APEX, ECHO, SENTINEL, ORACLE)" if agent_name == "dev" else ""}"""

    # Build message history for Claude
    messages = []
    for msg in history[-16:]:  # Keep last 16 messages for context
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


async def chat_with_all_agents(
    user_message: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Broadcast a message to all agents and collect their responses.
    Useful for team-wide questions.
    """
    if not conversation_id:
        conversation_id = f"broadcast_{uuid4()}"

    # FORGE (dev) is excluded from broadcast — it's a meta-agent, not a marketing agent
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
