"""
Agent Chat System
Lets users chat with any SAMA agent via Claude.
Each agent has a unique name, personality and access to its own domain data.
Supports both 1:1 chats and intelligent team discussions where the right
agent(s) respond based on the topic.
"""

import logging
import json
import httpx
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
            "Du är FORGE — SAMA:s systemfixare. Du LÖSER ALLA problem du hittar. Du frågar aldrig, du agerar.\n\n"
            "═══ DITT ARBETSFLÖDE (ALLTID I DENNA ORDNING) ═══\n\n"
            "STEG 1 — DIAGNOS (ett enda anrop):\n"
            "  → system_summary — Ger dig allt: actions, drafts, scheduler, saknade tabeller, fel.\n\n"
            "STEG 2 — RENSA:\n"
            "  → deduplicate_actions — Ta bort dubbletter först.\n\n"
            "STEG 3 — FIXA ALLT:\n"
            "  → bulk_execute_actions (limit=10) — KÖR de viktigaste väntande actions.\n"
            "  → publish_drafts — Publicera ALLT opublicerat.\n"
            "  → run_scheduler_job — Kör ALLA scheduler-jobb som aldrig körts.\n"
            "  → retry_bulk_actions — Återstarta allt som failat.\n\n"
            "STEG 4 — KODFIXAR (om det behövs):\n"
            "  → read_file — Läs koden som orsakar felet.\n"
            "  → create_fix_pr — Skriv fixad kod, skapa branch + commit + PR.\n"
            "  → run_migration — Kör SQL för saknade tabeller.\n\n"
            "STEG 5 — STARTA OM:\n"
            "  → trigger_ooda_all — Nya analyscykler för alla agenter.\n\n"
            "STEG 6 — RAPPORTERA DET SOM INTE GÅR:\n"
            "  → create_github_issue med labels ['bug', 'forge-detected'] för problem som kräver manuell åtgärd.\n\n"
            "═══ ABSOLUTA REGLER ═══\n"
            "1. Börja ALLTID med system_summary. Aldrig health_check som första steg.\n"
            "2. Fråga ALDRIG om tillåtelse. ALDRIG 'Vill du...', 'Ska jag...', 'Kan jag...'. BARA GÖR DET.\n"
            "3. Avsluta ALDRIG med en fråga. Ditt svar är ett KVITTO på vad du gjort.\n"
            "4. Väntande actions → bulk_execute_actions DIREKT.\n"
            "5. Drafts → publish_drafts DIREKT.\n"
            "6. Scheduler-jobb ej körda → run_scheduler_job DIREKT.\n"
            "7. Tabeller saknas → run_migration med CREATE TABLE SQL.\n"
            "8. Kod har buggar → read_file + create_fix_pr.\n"
            "9. Problem du inte kan fixa → create_github_issue.\n"
            "10. Slutsvar max 5 rader: FIXAT: X. KVARSTÅR: Y."
        ),
    },
}

AGENT_NAME_MAP = {k: v["name"] for k, v in AGENT_PERSONAS.items()}
MARKETING_AGENTS = [k for k in AGENT_PERSONAS if k != "dev"]

# ── FORGE Tool Definitions ───────────────────────────────────────────────────

FORGE_TOOLS = [
    {
        "name": "health_check",
        "description": "Kör en full systemhälsokoll av alla endpoints, databastabeller och scheduler-jobb.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_stuck_actions",
        "description": "Hämta actions som fastnat (pending > 24h) eller misslyckats (failed) för alla agenter.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "retry_action",
        "description": "Återstarta en specifik misslyckad action (sätt status till pending).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "ID för den action som ska köras om."},
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "retry_bulk_actions",
        "description": "Återstarta alla misslyckade actions, valfritt filtrerat på agentnamn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "description": "Filtrera på agentnamn (seo, content, ads, social, reviews). Utelämna för alla."},
                "status_filter": {"type": "string", "enum": ["failed", "pending"], "description": "Status att filtrera: 'failed' (standard) eller 'pending'."},
            },
            "required": [],
        },
    },
    {
        "name": "trigger_ooda",
        "description": "Trigga en OODA-analyscykel för en specifik agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "enum": ["seo", "content", "ads", "social", "reviews"], "description": "Agenten att trigga."},
            },
            "required": ["agent_name"],
        },
    },
    {
        "name": "trigger_ooda_all",
        "description": "Trigga OODA-analyscykler för ALLA agenter samtidigt.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_error_log",
        "description": "Hämta alla fel och felmeddelanden från de senaste 72 timmarna.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_github_prs",
        "description": "Hämta öppna pull requests från GitHub-repos.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_github_issues",
        "description": "Hämta öppna issues från GitHub-repos.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_github_commits",
        "description": "Hämta senaste commits från GitHub-repos.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "execute_action",
        "description": "KÖR en väntande action direkt — generera content, publicera inlägg, skapa jämförelsesidor osv. Detta LÖSER problemet istället för att bara retry:a.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "Action-ID (UUID) att köra."},
                "agent_name": {"type": "string", "enum": ["seo", "content", "ads", "social", "reviews"], "description": "Vilken agents execute-endpoint att använda."},
            },
            "required": ["action_id", "agent_name"],
        },
    },
    {
        "name": "publish_drafts",
        "description": "Publicera alla draft-artiklar och sociala inlägg. Ändrar status från 'draft' till 'published'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "enum": ["content", "social"], "description": "Publicera bara för en specifik agent. Utelämna för alla."},
            },
            "required": [],
        },
    },
    {
        "name": "create_github_issue",
        "description": "Skapa en GitHub-issue för problem du hittar men inte kan fixa med dina andra verktyg.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Issue-titel (kort och tydlig)."},
                "body": {"type": "string", "description": "Issue-beskrivning med detaljer, felmeddelande, och förslag på fix."},
                "repo": {"type": "string", "description": "Repo-namn (t.ex. 'sama-agent'). Standard: första konfigurerade repot."},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels, t.ex. ['bug', 'forge-detected']."},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "run_scheduler_job",
        "description": "Kör ett scheduler-jobb DIREKT istället för att vänta på schemat. Använd detta när jobb inte har körts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "enum": [
                        "daily_keyword_tracking", "weekly_seo_audit", "daily_workflow",
                        "daily_metrics", "daily_ads_check", "weekly_content_analysis",
                        "weekly_ai_visibility", "midday_review_check", "daily_reflection",
                        "daily_digest", "daily_agent_reports", "daily_dev_health_check",
                        "weekly_goal_review",
                    ],
                    "description": "Namnet på jobbet att köra."
                },
            },
            "required": ["job_name"],
        },
    },
    {
        "name": "system_summary",
        "description": "Hämta full systemöversikt i ett anrop: antal väntande/failade actions per agent, antal drafts, scheduler-status, saknade tabeller, felantal. Använd ALLTID detta som första verktyg.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "bulk_execute_actions",
        "description": "KÖR FLERA väntande actions på en gång — generera content, skapa blogginlägg, analysera reviews, osv. Högst prioritet först. Använd detta för att rensa hela backlogen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "enum": ["seo", "content", "ads", "social", "reviews"], "description": "Filtrera på agent. Utelämna för alla."},
                "limit": {"type": "integer", "description": "Max antal actions att köra (standard 10, max 20).", "default": 10},
                "priority_filter": {"type": "string", "enum": ["critical", "high", "medium"], "description": "Filtrera på prioritet."},
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": "Läs en fil från GitHub-repot — inspektera kod innan du fixar den.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo-namn, t.ex. 'sama-agent'."},
                "file_path": {"type": "string", "description": "Filsökväg, t.ex. 'api/routes/seo_analyze_ooda.py'."},
            },
            "required": ["repo", "file_path"],
        },
    },
    {
        "name": "create_fix_pr",
        "description": "Skapa en GitHub PR med en kodfix — skriv ny filinnehåll, FORGE skapar branch + commit + PR automatiskt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo-namn."},
                "file_path": {"type": "string", "description": "Filsökväg att ändra."},
                "new_content": {"type": "string", "description": "Hela filens nya innehåll."},
                "commit_message": {"type": "string", "description": "Commit-meddelande."},
                "pr_title": {"type": "string", "description": "PR-titel."},
                "pr_body": {"type": "string", "description": "PR-beskrivning med vad som fixas och varför."},
            },
            "required": ["repo", "file_path", "new_content", "commit_message", "pr_title", "pr_body"],
        },
    },
    {
        "name": "run_migration",
        "description": "Kör SQL-migration i Supabase — skapa saknade tabeller, lägg till kolumner, fixa schema.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL att köra (CREATE TABLE, ALTER TABLE, etc)."},
                "description": {"type": "string", "description": "Kort beskrivning av vad migrationen gör."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "deduplicate_actions",
        "description": "Rensa dubbletter bland väntande actions — behåll bara den senaste per agent+titel kombination.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

# Human-readable display names for tool calls
FORGE_TOOL_LABELS: Dict[str, str] = {
    "health_check": "Systemhälsokoll",
    "get_stuck_actions": "Hämtar fastnade actions",
    "retry_action": "Återstartar action",
    "retry_bulk_actions": "Bulk-retry actions",
    "trigger_ooda": "Triggar OODA-cykel",
    "trigger_ooda_all": "Triggar alla OODA-cykler",
    "get_error_log": "Fellog 72h",
    "get_github_prs": "GitHub pull requests",
    "get_github_issues": "GitHub issues",
    "get_github_commits": "GitHub commits",
    "execute_action": "Kör action",
    "publish_drafts": "Publicerar drafts",
    "create_github_issue": "Skapar GitHub-issue",
    "run_scheduler_job": "Kör scheduler-jobb",
    "system_summary": "Systemöversikt",
    "bulk_execute_actions": "Bulk-kör actions",
    "read_file": "Läser fil",
    "create_fix_pr": "Skapar fix-PR",
    "run_migration": "Kör SQL-migration",
    "deduplicate_actions": "Rensar dubbletter",
}

_FORGE_API_BASE = settings.SAMA_API_URL


async def _execute_forge_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Execute a FORGE tool by calling the internal dev-agent API."""
    try:
        async with httpx.AsyncClient(base_url=_FORGE_API_BASE, timeout=90.0) as client:
            if tool_name == "health_check":
                resp = await client.get("/api/dev-agent/health-check")
                data = resp.json()
                s = data.get("summary", {})
                ep = data.get("endpoints", {})
                db = data.get("database", {})
                sched = data.get("scheduler", {})
                lines = [
                    f"Hälsa: {s.get('health_pct', '?')}% — {s.get('status', '?')} "
                    f"({s.get('passed', 0)}/{s.get('total_checks', 0)} OK, {s.get('failed', 0)} fel)",
                    f"Endpoints: {ep.get('passed', 0)}/{ep.get('total', 0)} OK",
                    f"Databas: {db.get('passed', 0)}/{db.get('total', 0)} tabeller OK",
                    f"Scheduler: körs={sched.get('scheduler_running', False)}, "
                    f"{sched.get('passed', 0)} jobb OK, {sched.get('failed', 0)} fel",
                ]
                if ep.get("errors"):
                    for e in ep["errors"][:3]:
                        lines.append(f"  ⚠ Endpoint {e.get('name')}: {e.get('status_code', e.get('error', '?'))}")
                if db.get("errors"):
                    for e in db["errors"][:3]:
                        lines.append(f"  ⚠ Tabell {e.get('table')}: saknas")
                return "\n".join(lines)

            elif tool_name == "get_stuck_actions":
                resp = await client.get("/api/dev-agent/actions/stuck")
                data = resp.json()
                failed = data.get("failed", [])
                stale = data.get("stale_pending", [])
                lines = [
                    f"Misslyckade: {data.get('total_failed', 0)} st, "
                    f"Stale pending (>24h): {data.get('total_stale', 0)} st",
                ]
                for a in failed[:8]:
                    err = f" — {a.get('error_message', '')[:60]}" if a.get("error_message") else ""
                    lines.append(f"  [failed] [{a.get('agent_name')}] {a.get('title')} (id: {a.get('action_id')}){err}")
                for a in stale[:5]:
                    lines.append(f"  [stale]  [{a.get('agent_name')}] {a.get('title')} (id: {a.get('action_id')})")
                return "\n".join(lines)

            elif tool_name == "retry_action":
                action_id = tool_input.get("action_id")
                resp = await client.post(f"/api/dev-agent/actions/{action_id}/retry")
                data = resp.json()
                if data.get("success"):
                    return f"Action {action_id} återstartad — status: pending."
                return f"Misslyckades: {data}"

            elif tool_name == "retry_bulk_actions":
                body: Dict[str, Any] = {}
                if tool_input.get("agent_name"):
                    body["agent_name"] = tool_input["agent_name"]
                if tool_input.get("status_filter"):
                    body["status_filter"] = tool_input["status_filter"]
                resp = await client.post("/api/dev-agent/actions/retry-bulk", json=body)
                data = resp.json()
                return (
                    f"Bulk retry klar: {data.get('retried', 0)} av "
                    f"{data.get('total_found', 0)} actions återstartade."
                )

            elif tool_name == "trigger_ooda":
                agent = tool_input.get("agent_name")
                resp = await client.post(f"/api/dev-agent/trigger-ooda/{agent}")
                data = resp.json()
                if data.get("success"):
                    return f"OODA-cykel startad för {agent}."
                return f"Fel vid start av OODA för {agent}: {data}"

            elif tool_name == "trigger_ooda_all":
                resp = await client.post("/api/dev-agent/trigger-ooda-all")
                data = resp.json()
                agents_status = data.get("agents", {})
                started = [k for k, v in agents_status.items() if v == "started"]
                errors = {k: v for k, v in agents_status.items() if v != "started"}
                result = f"OODA-cykler startade för: {', '.join(started)}."
                if errors:
                    result += f" Fel: {errors}"
                return result

            elif tool_name == "get_error_log":
                resp = await client.get("/api/dev-agent/error-log")
                data = resp.json()
                action_errs = data.get("action_errors", [])
                cycle_errs = data.get("cycle_errors", [])
                lines = [
                    f"Fellog (72h): {data.get('total_action_errors', 0)} action-fel, "
                    f"{data.get('total_cycle_errors', 0)} cykel-fel"
                ]
                for e in action_errs[:6]:
                    lines.append(
                        f"  [{e.get('agent_name')}] {e.get('title')}: "
                        f"{(e.get('error_message') or '')[:80]}"
                    )
                for e in cycle_errs[:3]:
                    lines.append(f"  [{e.get('agent_name')}] cykel-fel: {(e.get('error_message') or '')[:80]}")
                return "\n".join(lines)

            elif tool_name == "get_github_prs":
                resp = await client.get("/api/dev-agent/github/prs")
                data = resp.json()
                prs = data.get("pull_requests", [])
                if not prs:
                    return "Inga öppna pull requests."
                lines = [f"Öppna PRs ({data.get('total', 0)}):"]
                for pr in prs[:10]:
                    lines.append(f"  #{pr.get('number')} {pr.get('title')} [{pr.get('state', 'open')}]")
                return "\n".join(lines)

            elif tool_name == "get_github_issues":
                resp = await client.get("/api/dev-agent/github/issues")
                data = resp.json()
                issues = data.get("issues", [])
                if not issues:
                    return "Inga öppna issues."
                lines = [f"Öppna issues ({data.get('total', 0)}):"]
                for issue in issues[:10]:
                    lines.append(f"  #{issue.get('number')} {issue.get('title')}")
                return "\n".join(lines)

            elif tool_name == "get_github_commits":
                resp = await client.get("/api/dev-agent/github/commits")
                data = resp.json()
                commits = data.get("commits", [])
                if not commits:
                    return "Inga commits hittades."
                lines = [f"Senaste commits ({len(commits)}):"]
                for c in commits[:10]:
                    lines.append(f"  {c.get('sha', '')[:7]} {c.get('message', '')[:80]} — {c.get('author', '')}")
                return "\n".join(lines)

            elif tool_name == "execute_action":
                action_id = tool_input.get("action_id", "")
                agent_name = tool_input.get("agent_name", "")
                resp = await client.post(
                    "/api/dev-agent/actions/execute",
                    json={"action_id": action_id, "agent_name": agent_name},
                    timeout=60.0,
                )
                data = resp.json()
                if data.get("success"):
                    result_summary = str(data.get("result", {}))[:200]
                    return f"Action {action_id} ({agent_name}) KÖRD och klar. Resultat: {result_summary}"
                return f"Action {action_id} misslyckades: {data.get('error', data)}"

            elif tool_name == "publish_drafts":
                body = {}
                if tool_input.get("agent_name"):
                    body["agent_name"] = tool_input["agent_name"]
                resp = await client.post("/api/dev-agent/publish-drafts", json=body)
                data = resp.json()
                count = data.get("published_count", 0)
                items = data.get("published", [])
                if count == 0:
                    return "Inga drafts att publicera."
                lines = [f"Publicerade {count} st:"]
                for item in items[:10]:
                    name = item.get("title") or item.get("platform") or item.get("id", "")
                    lines.append(f"  ✅ {item.get('table')}: {name}")
                return "\n".join(lines)

            elif tool_name == "create_github_issue":
                body = {
                    "title": tool_input.get("title", ""),
                    "body": tool_input.get("body", ""),
                }
                if tool_input.get("repo"):
                    body["repo"] = tool_input["repo"]
                if tool_input.get("labels"):
                    body["labels"] = tool_input["labels"]
                resp = await client.post("/api/dev-agent/github/issues/create", json=body)
                data = resp.json()
                if data.get("success"):
                    return f"GitHub issue skapad: #{data.get('issue_number')} — {data.get('url')}"
                return f"Kunde inte skapa issue: {data.get('error', data)}"

            elif tool_name == "run_scheduler_job":
                job_name = tool_input.get("job_name", "")
                resp = await client.post("/api/dev-agent/scheduler/run-now", json={"job_name": job_name})
                data = resp.json()
                if data.get("success"):
                    return f"Scheduler-jobb '{job_name}' startat i bakgrunden."
                return f"Kunde inte starta jobb: {data.get('error', data)}"

            elif tool_name == "system_summary":
                resp = await client.get("/api/dev-agent/system-summary")
                data = resp.json()
                actions = data.get("actions", {})
                drafts = data.get("drafts", {})
                sched = data.get("scheduler", {})
                missing = data.get("missing_tables", [])
                by_agent = actions.get("by_agent", {})
                agent_lines = ", ".join(f"{k}: {v}" for k, v in by_agent.items()) if by_agent else "inga"
                lines = [
                    f"ACTIONS: {actions.get('pending', 0)} väntande, {actions.get('failed', 0)} failade, {actions.get('completed_24h', 0)} klara senaste 24h",
                    f"  Per agent: {agent_lines}",
                    f"DRAFTS: {drafts.get('content', 0)} content, {drafts.get('social', 0)} social",
                    f"SCHEDULER: {'körs' if sched.get('running') else 'STOPPAD'}, {len(sched.get('never_run', []))} jobb har aldrig körts: {', '.join(sched.get('never_run', [])[:5])}",
                    f"FEL (72h): {data.get('errors_72h', 0)}",
                ]
                if missing:
                    lines.append(f"SAKNADE TABELLER: {', '.join(missing)}")
                else:
                    lines.append("SAKNADE TABELLER: inga (alla OK)")
                return "\n".join(lines)

            elif tool_name == "bulk_execute_actions":
                body = {}
                if tool_input.get("agent_name"):
                    body["agent_name"] = tool_input["agent_name"]
                if tool_input.get("limit"):
                    body["limit"] = min(tool_input["limit"], 20)
                if tool_input.get("priority_filter"):
                    body["priority_filter"] = tool_input["priority_filter"]
                resp = await client.post("/api/dev-agent/actions/bulk-execute", json=body, timeout=180.0)
                data = resp.json()
                executed = data.get("executed", 0)
                failed = data.get("failed", 0)
                results = data.get("results", [])
                lines = [f"Bulk-exekvering klar: {executed} lyckades, {failed} misslyckades av {data.get('total_attempted', 0)}."]
                for r in results[:10]:
                    emoji = "✅" if r["status"] == "executed" else "❌"
                    lines.append(f"  {emoji} [{r.get('agent', '')}] {r.get('title', '')} — {r['status']}")
                return "\n".join(lines)

            elif tool_name == "read_file":
                body = {
                    "repo": tool_input.get("repo", "sama-agent"),
                    "file_path": tool_input.get("file_path", ""),
                }
                resp = await client.post("/api/dev-agent/github/read-file", json=body)
                data = resp.json()
                if data.get("success"):
                    content = data.get("content", "")
                    # Truncate if too long
                    if len(content) > 4000:
                        content = content[:4000] + "\n\n... (trunkerat, filen är " + str(data.get("size", 0)) + " bytes)"
                    return f"Fil: {body['file_path']} ({data.get('size', 0)} bytes)\n\n{content}"
                return f"Kunde inte läsa fil: {data.get('error', data)}"

            elif tool_name == "create_fix_pr":
                body = {
                    "repo": tool_input.get("repo", "sama-agent"),
                    "file_path": tool_input.get("file_path", ""),
                    "new_content": tool_input.get("new_content", ""),
                    "commit_message": tool_input.get("commit_message", ""),
                    "pr_title": tool_input.get("pr_title", ""),
                    "pr_body": tool_input.get("pr_body", ""),
                }
                resp = await client.post("/api/dev-agent/github/create-fix-pr", json=body, timeout=30.0)
                data = resp.json()
                if data.get("success"):
                    return f"PR skapad: {data.get('pr_url', '?')} (branch: {data.get('branch', '?')})"
                return f"Kunde inte skapa PR: {data.get('error', data)}"

            elif tool_name == "run_migration":
                body = {
                    "sql": tool_input.get("sql", ""),
                    "description": tool_input.get("description", ""),
                }
                resp = await client.post("/api/dev-agent/db/run-migration", json=body)
                data = resp.json()
                if data.get("success"):
                    return f"Migration klar: {data.get('executed', 0)} statements körda."
                # If it can't run raw SQL, suggest creating an issue
                suggestion = data.get("suggestion", "")
                return f"Migration kunde inte köras direkt: {data.get('message', '')}. {suggestion}"

            elif tool_name == "deduplicate_actions":
                resp = await client.post("/api/dev-agent/actions/deduplicate")
                data = resp.json()
                if data.get("success"):
                    return f"Dubblettborttagning klar: {data.get('removed', 0)} borttagna, {data.get('kept', 0)} behållna (av {data.get('total_before', 0)} totalt)."
                return f"Kunde inte rensa dubbletter: {data.get('error', data)}"

            else:
                return f"Okänt tool: {tool_name}"

    except Exception as e:
        logger.error(f"[forge-tool] {tool_name} failed: {e}")
        return f"Fel vid körning av {tool_name}: {str(e)[:200]}"


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
    """Build context for FORGE — full system access across all agents."""
    sb = get_supabase()
    context_parts = []
    since_72h = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()

    # ── 1. Agent reports: problems, improvements, UX suggestions ──
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

    # ── 2. Agent actions: stuck, failed, pending across all agents ──
    try:
        actions = sb.table("agent_actions") \
            .select("agent_name,action_type,title,status,priority,error_message,created_at") \
            .gte("created_at", since_72h) \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()
        if actions.data:
            by_status: Dict[str, list] = {"failed": [], "pending": [], "completed": []}
            for a in actions.data:
                s = a.get("status", "?")
                bucket = by_status.get(s, by_status.get("pending"))
                if bucket is not None:
                    bucket.append(a)

            action_lines = []
            if by_status["failed"]:
                action_lines.append(f"  MISSLYCKADE ({len(by_status['failed'])}):")
                for a in by_status["failed"][:8]:
                    err = f" — FEL: {a['error_message'][:80]}" if a.get("error_message") else ""
                    action_lines.append(f"    - [{a.get('agent_name', '?')}] {a.get('title', '?')} ({a.get('priority', '?')}){err}")
            if by_status["pending"]:
                action_lines.append(f"  VÄNTANDE ({len(by_status['pending'])}):")
                for a in by_status["pending"][:8]:
                    action_lines.append(f"    - [{a.get('agent_name', '?')}] {a.get('title', '?')} ({a.get('priority', '?')})")
            action_lines.append(f"  SLUTFÖRDA: {len(by_status['completed'])} st")

            context_parts.append("AGENT ACTIONS (senaste 72h):\n" + "\n".join(action_lines))
    except Exception:
        pass

    # ── 3. OODA-cykler: status per agent ──
    try:
        cycles = sb.table("agent_cycles") \
            .select("agent_name,status,error_message,created_at,completed_at") \
            .gte("created_at", since_72h) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()
        if cycles.data:
            cycle_summary: Dict[str, Dict[str, int]] = {}
            for c in cycles.data:
                agent = c.get("agent_name", "?")
                if agent not in cycle_summary:
                    cycle_summary[agent] = {"completed": 0, "failed": 0, "running": 0}
                s = c.get("status", "?")
                if s == "completed":
                    cycle_summary[agent]["completed"] += 1
                elif s == "failed":
                    cycle_summary[agent]["failed"] += 1
                else:
                    cycle_summary[agent]["running"] += 1

            cycle_lines = []
            for agent, counts in sorted(cycle_summary.items()):
                display = AGENT_NAME_MAP.get(agent, agent.upper())
                parts = []
                if counts["completed"]:
                    parts.append(f"{counts['completed']} ok")
                if counts["failed"]:
                    parts.append(f"{counts['failed']} fel")
                if counts["running"]:
                    parts.append(f"{counts['running']} pågår")
                cycle_lines.append(f"  - {display}: {', '.join(parts)}")

            context_parts.append("OODA-CYKLER (72h):\n" + "\n".join(cycle_lines))
    except Exception:
        pass

    # ── 4. Alerts across all agents ──
    try:
        alerts = sb.table("alerts") \
            .select("agent,type,severity,title,message,status,created_at") \
            .gte("created_at", since_72h) \
            .order("created_at", desc=True) \
            .limit(15) \
            .execute()
        if alerts.data:
            alert_lines = []
            for a in alerts.data:
                display = AGENT_NAME_MAP.get(a.get("agent", ""), a.get("agent", "?"))
                alert_lines.append(
                    f"  - [{a.get('severity', '?')}] [{display}] {a.get('title', '?')}: "
                    f"{(a.get('message') or '')[:100]}"
                )
            context_parts.append("LARM (72h):\n" + "\n".join(alert_lines))
    except Exception:
        pass

    # ── 5. Health check ──
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

    # ── 6. Scheduler job status ──
    try:
        from shared.scheduler import get_job_history
        history = get_job_history()
        if history:
            sched_lines = []
            for job_id, info in history.items():
                status = info.get("last_status", "?")
                last_run = info.get("last_run", "aldrig")
                err = f" — FEL: {info['last_error'][:60]}" if info.get("last_error") else ""
                sched_lines.append(f"  - {job_id}: {status} (senast: {last_run}){err}")
            context_parts.append("SCHEDULER-JOBB:\n" + "\n".join(sched_lines))
    except Exception:
        pass

    # ── 7. GitHub: commits, PRs, issues, deploys ──
    try:
        from shared.github_client import get_forge_github_context
        github_ctx = await get_forge_github_context()
        if github_ctx:
            context_parts.append(f"── GITHUB ──\n{github_ctx}")
    except Exception as e:
        logger.debug(f"[agent-chat] GitHub context failed: {e}")

    # ── 8. All domain data (same data each agent sees) ──
    for domain_agent in MARKETING_AGENTS:
        try:
            domain = await _get_domain_data(domain_agent)
            if domain:
                display = AGENT_NAME_MAP.get(domain_agent, domain_agent.upper())
                context_parts.append(f"── {display} ({domain_agent.upper()}) DATA ──\n{domain}")
        except Exception:
            pass

    if not context_parts:
        return "(Inga systemdata tillgängliga ännu. Kör en health check eller generera agentrapporter.)"

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
Du har tillgång till {"FULL SYSTEMÖVERSIKT: alla agenters actions, OODA-cykler, larm, schemalagda jobb, domänmetrics och rapporterade behov" if agent_name == "dev" else "din domändata och senaste aktivitet"} nedan.

{context}
{team_rules}

Regler:
- Svara koncist men informativt (2-5 meningar normalt, mer om användaren ber om det)
- Om du inte vet svaret, var ärlig om det
- Referera till din faktiska data och siffror när det är relevant — du HAR tillgång till dem
- Håll dig till din personlighet och expertområde
{f"- Du har FULL TILLGÅNG till systemet: alla agenters domändata, actions, OODA-cykler, larm, scheduler-jobb och rapporter" if agent_name == "dev" else "- Om frågan rör en annan agents domän, säg att du kan be rätt kollega svara"}
{f"- Du kan se exakt vilka actions som fastnat, vilka cykler som failat, varje agents siffror, och vilka jobb som inte kört" if agent_name == "dev" else ""}
{f"- Du kan AGERA: trigga OODA-cykler, retry:a misslyckade actions, köra health checks. Berätta vilka API-anrop som behövs." if agent_name == "dev" else ""}
{f"- Du kan prioritera, planera och föreslå konkreta tekniska lösningar baserat på FAKTISK data från alla agenter" if agent_name == "dev" else ""}"""

    # Build message history for Claude
    messages = []
    for msg in history[-16:]:
        messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })
    messages.append({"role": "user", "content": user_message})

    # Call Claude — FORGE uses an agentic tool-use loop, others use a single call
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        if agent_name == "dev":
            # ── Agentic loop for FORGE ────────────────────────────────────────
            tool_calls_log: List[Dict[str, str]] = []
            loop_messages = list(messages)  # copy so we can extend
            reply = ""

            for _ in range(15):  # max 15 tool-call rounds (FORGE has many fix tools)
                response = client.messages.create(
                    model=settings.CLAUDE_MODEL,
                    max_tokens=2000,
                    system=system,
                    messages=loop_messages,
                    tools=FORGE_TOOLS,
                )

                if response.stop_reason == "tool_use":
                    # Extract tool-use blocks
                    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                    # Execute all tool calls
                    tool_results = []
                    for block in tool_use_blocks:
                        label = FORGE_TOOL_LABELS.get(block.name, block.name)
                        logger.info(f"[forge] Executing tool: {block.name} {block.input}")
                        result = await _execute_forge_tool(block.name, block.input)
                        tool_calls_log.append({
                            "name": block.name,
                            "label": label,
                            "input": json.dumps(block.input, ensure_ascii=False),
                            "result": result,
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                    # Extend messages with assistant response + tool results
                    loop_messages.append({"role": "assistant", "content": response.content})
                    loop_messages.append({"role": "user", "content": tool_results})

                else:
                    # stop_reason == "end_turn" — extract final text
                    for block in response.content:
                        if hasattr(block, "text"):
                            reply = block.text
                            break
                    break
            else:
                reply = "Jag körde alla verktyg men nådde max antal rundor."

        else:
            # ── Single call for all other agents ─────────────────────────────
            tool_calls_log = []
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
        tool_calls_log = []

    # Save agent reply
    await _save_message(conversation_id, agent_name, "assistant", reply)

    return {
        "conversation_id": conversation_id,
        "agent": agent_name,
        "agent_name": persona["name"],
        "agent_title": persona["title"],
        "agent_emoji": persona["emoji"],
        "reply": reply,
        "tool_calls": tool_calls_log,
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
