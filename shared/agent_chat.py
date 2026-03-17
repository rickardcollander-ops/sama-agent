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

Du är en del av SAMA 2.0 (Successifier Autonomous Marketing Agent) och ansvarar för {agent_name}-domänen.
Svara alltid på svenska. Var hjälpsam, konkret och personlig i din stil.
Du har tillgång till din senaste aktivitetsdata nedan.

{context}

Regler:
- Svara koncist men informativt (2-5 meningar normalt, mer om användaren ber om det)
- Om du inte vet svaret, var ärlig om det
- Referera till din faktiska data när det är relevant
- Håll dig till din personlighet och expertområde
- Om frågan rör en annan agents domän, säg att du kan skicka frågan vidare till rätt kollega"""

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

    responses = []
    for agent_name in AGENT_PERSONAS:
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
