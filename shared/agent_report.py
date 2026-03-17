"""
Agent Self-Report System
Each agent generates a daily summary of what it did in the last 24 hours
and what it needs improved to do a better job.
Claude analyzes raw activity data and produces a natural-language report.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from shared.database import get_supabase
from shared.config import settings

logger = logging.getLogger(__name__)

AGENT_NAMES = ["seo", "content", "ads", "social", "reviews", "analytics"]


async def _fetch_agent_activity(agent_name: str, since: str) -> Dict[str, Any]:
    """Fetch raw activity data for an agent from the last 24h."""
    sb = get_supabase()
    activity: Dict[str, Any] = {"agent": agent_name}

    # Actions (created or executed)
    try:
        actions = sb.table("agent_actions") \
            .select("action_type,title,status,priority,created_at,executed_at,error_message") \
            .eq("agent_name", agent_name) \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()
        activity["actions"] = actions.data or []
    except Exception:
        activity["actions"] = []

    # OODA cycles
    try:
        cycles = sb.table("agent_cycles") \
            .select("status,created_at,completed_at,error_message") \
            .eq("agent_name", agent_name) \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(10) \
            .execute()
        activity["cycles"] = cycles.data or []
    except Exception:
        activity["cycles"] = []

    # Alerts generated
    try:
        alerts = sb.table("alerts") \
            .select("type,severity,title,message,status") \
            .eq("agent", agent_name) \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()
        activity["alerts"] = alerts.data or []
    except Exception:
        activity["alerts"] = []

    # Learnings
    try:
        learnings = sb.table("agent_learnings") \
            .select("learning_type,context,confidence_score") \
            .eq("agent_name", agent_name) \
            .gte("created_at", since) \
            .limit(10) \
            .execute()
        activity["learnings"] = learnings.data or []
    except Exception:
        activity["learnings"] = []

    return activity


async def generate_agent_report(agent_name: str) -> Dict[str, Any]:
    """
    Generate a self-report for one agent using Claude.
    Returns: {agent, summary, actions_summary, improvements, generated_at}
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    activity = await _fetch_agent_activity(agent_name, since)

    # Count stats
    actions = activity.get("actions", [])
    cycles = activity.get("cycles", [])
    alerts = activity.get("alerts", [])
    learnings = activity.get("learnings", [])

    completed = [a for a in actions if a.get("status") in ("completed", "auto_executed")]
    pending = [a for a in actions if a.get("status") == "pending"]
    failed = [a for a in actions if a.get("status") == "failed"]
    errors = [a for a in actions if a.get("error_message")]

    stats = {
        "actions_created": len(actions),
        "actions_completed": len(completed),
        "actions_pending": len(pending),
        "actions_failed": len(failed),
        "ooda_cycles": len(cycles),
        "cycles_completed": len([c for c in cycles if c.get("status") == "completed"]),
        "cycles_failed": len([c for c in cycles if c.get("status") == "failed"]),
        "alerts_raised": len(alerts),
        "critical_alerts": len([a for a in alerts if a.get("severity") == "critical"]),
        "learnings_recorded": len(learnings),
    }

    # Build prompt for Claude
    action_lines = []
    for a in actions[:20]:
        line = f"- [{a.get('status', '?')}] {a.get('title', 'No title')} (type: {a.get('action_type', '?')}, priority: {a.get('priority', '?')})"
        if a.get("error_message"):
            line += f" ERROR: {a['error_message'][:100]}"
        action_lines.append(line)

    alert_lines = [
        f"- [{a.get('severity', '?')}] {a.get('title', '')}: {a.get('message', '')[:100]}"
        for a in alerts[:10]
    ]

    learning_lines = [
        f"- [{l.get('learning_type', '?')}] {l.get('context', '')[:100]} (confidence: {l.get('confidence_score', '?')})"
        for l in learnings[:5]
    ]

    prompt = f"""Du är {agent_name}-agenten i SAMA 2.0 (Successifier Autonomous Marketing Agent).
Skriv en kort daglig statusrapport baserat på din aktivitet de senaste 24 timmarna.

STATISTIK:
- Actions skapade: {stats['actions_created']}
- Actions slutförda: {stats['actions_completed']}
- Actions väntande: {stats['actions_pending']}
- Actions misslyckade: {stats['actions_failed']}
- OODA-cykler: {stats['ooda_cycles']} (slutförda: {stats['cycles_completed']}, misslyckade: {stats['cycles_failed']})
- Larm: {stats['alerts_raised']} (kritiska: {stats['critical_alerts']})
- Lärdomar: {stats['learnings_recorded']}

ACTIONS (senaste 24h):
{chr(10).join(action_lines) if action_lines else '(inga actions)'}

LARM:
{chr(10).join(alert_lines) if alert_lines else '(inga larm)'}

LÄRDOMAR:
{chr(10).join(learning_lines) if learning_lines else '(inga nya lärdomar)'}

Svara i JSON-format:
{{
  "summary": "2-3 meningar som sammanfattar vad du gjort",
  "highlights": ["lista med de viktigaste sakerna du gjort (max 5)"],
  "problems": ["lista med problem eller fel som uppstått (max 5)"],
  "improvements": ["konkreta förslag på vad som behöver förbättras i systemet för att du ska kunna göra ett bättre jobb (max 5). Var specifik — nämn API:er som saknas, data som fattas, funktionalitet som behöver byggas, etc."],
  "ux_suggestions": ["konkreta UX-förbättringsförslag för dashboarden och användarupplevelsen (max 5). Tänk på: vilken data borde visas bättre? Vilka knappar/vyer saknas? Vad gör det svårt för en människa att förstå vad du gör? Vilka visualiseringar skulle hjälpa?"]
}}

Skriv på svenska. Om det inte fanns någon aktivitet, rapportera det och föreslå förbättringar ändå baserat på din roll."""

    # Call Claude
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = response.content[0].text
        # Extract JSON from response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            report_data = json.loads(text[start:end])
        else:
            report_data = {"summary": text, "highlights": [], "problems": [], "improvements": [], "ux_suggestions": []}
    except Exception as e:
        logger.warning(f"[agent-report] Claude call failed for {agent_name}: {e}")
        # Fallback: generate a basic report from stats
        report_data = _fallback_report(agent_name, stats, errors)

    report = {
        "agent": agent_name,
        "stats": stats,
        "summary": report_data.get("summary", ""),
        "highlights": report_data.get("highlights", []),
        "problems": report_data.get("problems", []),
        "improvements": report_data.get("improvements", []),
        "ux_suggestions": report_data.get("ux_suggestions", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Persist to Supabase
    await _save_report(report)

    return report


def _fallback_report(agent_name: str, stats: Dict, errors: List) -> Dict:
    """Generate a basic report without Claude if API is unavailable."""
    summary = f"{agent_name}-agenten skapade {stats['actions_created']} actions "
    summary += f"({stats['actions_completed']} slutförda, {stats['actions_failed']} misslyckade). "
    summary += f"{stats['ooda_cycles']} OODA-cykler kördes."

    highlights = []
    if stats["actions_completed"] > 0:
        highlights.append(f"{stats['actions_completed']} actions slutförda")
    if stats["learnings_recorded"] > 0:
        highlights.append(f"{stats['learnings_recorded']} nya lärdomar registrerade")

    problems = []
    if stats["actions_failed"] > 0:
        problems.append(f"{stats['actions_failed']} actions misslyckades")
    for e in errors[:3]:
        if e.get("error_message"):
            problems.append(e["error_message"][:100])

    improvements = ["Kunde inte generera detaljerad rapport — kontrollera ANTHROPIC_API_KEY"]

    return {
        "summary": summary,
        "highlights": highlights,
        "problems": problems,
        "improvements": improvements,
        "ux_suggestions": [],
    }


async def _save_report(report: Dict[str, Any]):
    """Save report to Supabase for history and dev agent consumption."""
    try:
        sb = get_supabase()
        sb.table("agent_reports").insert({
            "agent_name": report["agent"],
            "summary": report["summary"],
            "highlights": report["highlights"],
            "problems": report["problems"],
            "improvements": report["improvements"],
            "ux_suggestions": report.get("ux_suggestions", []),
            "stats": report["stats"],
            "created_at": report["generated_at"],
        }).execute()
    except Exception as e:
        logger.debug(f"[agent-report] Could not save report: {e}")


async def generate_all_reports() -> List[Dict[str, Any]]:
    """Generate reports for all agents."""
    reports = []
    for agent_name in AGENT_NAMES:
        try:
            report = await generate_agent_report(agent_name)
            reports.append(report)
            logger.info(f"[agent-report] Generated report for {agent_name}")
        except Exception as e:
            logger.warning(f"[agent-report] Failed to generate report for {agent_name}: {e}")
            reports.append({
                "agent": agent_name,
                "summary": f"Kunde inte generera rapport: {e}",
                "highlights": [],
                "problems": [str(e)],
                "improvements": [],
                "stats": {},
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })
    return reports


async def get_latest_reports() -> List[Dict[str, Any]]:
    """Get the most recent report for each agent from Supabase."""
    reports = []
    try:
        sb = get_supabase()
        for agent_name in AGENT_NAMES:
            result = sb.table("agent_reports") \
                .select("*") \
                .eq("agent_name", agent_name) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            if result.data:
                reports.append(result.data[0])
    except Exception as e:
        logger.debug(f"[agent-report] Could not fetch reports: {e}")
    return reports


async def get_all_improvements() -> List[Dict[str, str]]:
    """
    Get all improvement suggestions from the latest reports.
    Used by the dev agent to pick up what needs fixing.
    """
    reports = await get_latest_reports()
    improvements = []
    for r in reports:
        agent = r.get("agent_name", "unknown")
        for item in r.get("improvements", []):
            improvements.append({"agent": agent, "type": "system", "suggestion": item})
        for item in r.get("problems", []):
            improvements.append({"agent": agent, "type": "problem", "problem": item})
        for item in r.get("ux_suggestions", []):
            improvements.append({"agent": agent, "type": "ux", "suggestion": item})
    return improvements
