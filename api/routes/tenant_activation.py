"""
Tenant Activation & Agent Control API Routes
Activate tenants, manage agent configs, trigger manual runs, view run history.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

ALL_AGENTS = ["seo", "content", "social", "ads", "reviews", "analytics", "geo"]

DEFAULT_SCHEDULES = {
    "seo": "daily",
    "content": "weekly",
    "social": "daily",
    "ads": "manual",
    "reviews": "daily",
    "analytics": "daily",
    "geo": "weekly",
}

# A single agent cycle should never legitimately take longer than this.
# Anything past it is treated as orphaned (process restart, deadlock, etc.).
STALE_RUN_AFTER = timedelta(minutes=15)


# ── POST /activate — Initial setup for a new tenant ─────────────────────────

@router.post("/activate")
async def activate_tenant(request: Request):
    """Run initial setup: discover keywords, create first content, init agent configs."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    if not tenant_id or tenant_id == "default":
        raise HTTPException(status_code=400, detail="Tenant ID required")

    sb = get_supabase()
    keywords_added = 0
    content_created = 0

    try:
        # 1. Load brand context from user_settings
        brand = {}
        try:
            data = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
            brand = data.data.get("settings", {}) if data.data else {}
        except Exception as e:
            logger.warning(f"Could not load brand settings for {tenant_id}: {e}")

        brand_name = brand.get("brand_name", "")
        domain = brand.get("domain", "")
        target_audience = brand.get("target_audience", "")
        competitors = brand.get("competitors", [])
        brand_description = brand.get("brand_description", "")

        # 2. Discover keywords using Claude AI
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            prompt = f"""You are an SEO expert. Suggest 12 high-value keywords for the following business:

Brand: {brand_name}
Website: {domain}
Description: {brand_description}
Target audience: {target_audience}
Competitors: {', '.join(competitors) if competitors else 'N/A'}

Return ONLY a JSON array of keyword strings, no markdown, no code fences. Example:
["keyword 1", "keyword 2", "keyword 3"]

Focus on:
- Commercial intent keywords (people ready to buy/compare)
- Informational keywords (people researching the topic)
- Long-tail keywords with lower competition
- Keywords the competitors likely target
"""
            message = await client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text.strip()
            try:
                keywords = json.loads(text)
            except json.JSONDecodeError:
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    keywords = json.loads(text.strip())
                else:
                    keywords = []

            if isinstance(keywords, list) and keywords:
                for kw in keywords:
                    if isinstance(kw, str) and kw.strip():
                        try:
                            sb.table("seo_keywords").insert({
                                "keyword": kw.strip(),
                                "tenant_id": tenant_id,
                                "source": "ai_activation",
                            }).execute()
                            keywords_added += 1
                        except Exception:
                            pass  # duplicate or schema issue
        except Exception as e:
            logger.error(f"Keyword discovery failed for {tenant_id}: {e}")

        # 3. Generate one LinkedIn post draft
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            content_prompt = f"""You are an expert B2B SaaS content marketer.
Generate a LinkedIn Post based on the following brief:

Topic: Choose a relevant topic
Brand: {brand_description or brand_name}
Target audience: {target_audience}
Tone: professional

Return ONLY a JSON object (no markdown, no code fences) with these keys:
{{
  "title": "...",
  "body": "...",
  "platform": "linkedin_post",
  "suggestions": ["improvement suggestion 1", "improvement suggestion 2"]
}}

For linkedin_post: body should be 100-200 words, optimized for LinkedIn.
"""
            message = await client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": content_prompt}],
            )
            ctext = message.content[0].text.strip()
            try:
                content_result = json.loads(ctext)
            except json.JSONDecodeError:
                if "```" in ctext:
                    ctext = ctext.split("```")[1]
                    if ctext.startswith("json"):
                        ctext = ctext[4:]
                    content_result = json.loads(ctext.strip())
                else:
                    content_result = None

            if content_result:
                try:
                    sb.table("content_pieces").insert({
                        "tenant_id": tenant_id,
                        "title": content_result.get("title", "LinkedIn Draft"),
                        "body": content_result.get("body", ""),
                        "platform": "linkedin_post",
                        "status": "draft",
                    }).execute()
                    content_created = 1
                except Exception as e:
                    logger.error(f"Failed to save content for {tenant_id}: {e}")
        except Exception as e:
            logger.error(f"Content generation failed for {tenant_id}: {e}")

        # 4. Initialize agent configs for this tenant
        for agent_name in ALL_AGENTS:
            try:
                sb.table("tenant_agent_config").upsert({
                    "tenant_id": tenant_id,
                    "agent_name": agent_name,
                    "enabled": agent_name != "ads",  # ads disabled by default
                    "schedule": DEFAULT_SCHEDULES.get(agent_name, "daily"),
                }, on_conflict="tenant_id,agent_name").execute()
            except Exception:
                pass

        return {
            "status": "activated",
            "tenant_id": tenant_id,
            "keywords_added": keywords_added,
            "content_created": content_created,
        }

    except Exception as e:
        logger.error(f"activate_tenant error for {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /agent-status — Agent config for this tenant ─────────────────────────

@router.get("/agent-status")
async def get_agent_status(request: Request):
    """Return agent configs + last run info for the tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    agents = []
    try:
        result = sb.table("tenant_agent_config").select("*").eq("tenant_id", tenant_id).execute()
        configs = {row["agent_name"]: row for row in (result.data or [])}
    except Exception:
        configs = {}

    for name in ALL_AGENTS:
        cfg = configs.get(name, {})
        agents.append({
            "name": name,
            "enabled": cfg.get("enabled", True),
            "schedule": cfg.get("schedule", DEFAULT_SCHEDULES.get(name, "daily")),
            "last_run": cfg.get("last_run_at"),
        })

    return {"agents": agents}


# ── POST /agents/{agent_name}/toggle — Enable/disable an agent ──────────────

class TogglePayload(BaseModel):
    enabled: bool


@router.post("/agents/{agent_name}/toggle")
async def toggle_agent(agent_name: str, payload: TogglePayload, request: Request):
    """Enable or disable a specific agent for this tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    if agent_name not in ALL_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_name}")

    sb = get_supabase()
    try:
        sb.table("tenant_agent_config").upsert({
            "tenant_id": tenant_id,
            "agent_name": agent_name,
            "enabled": payload.enabled,
            "schedule": DEFAULT_SCHEDULES.get(agent_name, "daily"),
        }, on_conflict="tenant_id,agent_name").execute()
        return {"success": True, "agent": agent_name, "enabled": payload.enabled}
    except Exception as e:
        logger.error(f"toggle_agent error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers: agent dispatch + run record management ──────────────────────────

async def _dispatch_agent_cycle(agent_name: str, tenant_id: str) -> str:
    """
    Run one cycle of the given agent for the given tenant. Returns a short
    human-readable summary. Raises on failure so the caller can mark the
    agent_runs row as failed with the exception message.
    """
    from shared.tenant_agents import AGENT_FACTORIES, get_agent
    if agent_name not in AGENT_FACTORIES:
        return f"{agent_name} triggered"
    agent = await get_agent(agent_name, tenant_id)
    result = await agent.run_cycle()
    return result or f"{agent_name} cycle completed"


async def _execute_run(run_id: Optional[str], tenant_id: str, agent_name: str) -> None:
    """
    Background task: runs the agent cycle and updates the agent_runs row.
    Never raises — failures are recorded as status=failed.
    """
    sb = get_supabase()
    started = datetime.now(timezone.utc).isoformat()
    try:
        summary = await _dispatch_agent_cycle(agent_name, tenant_id)
        status = "completed"
        error_msg: Optional[str] = None
    except Exception as e:
        summary = ""
        status = "failed"
        error_msg = str(e)[:500]
        logger.exception(f"Agent {agent_name} run failed for {tenant_id}")

    if run_id:
        try:
            update = {
                "status": status,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
            }
            if error_msg:
                update["error"] = error_msg
            sb.table("agent_runs").update(update).eq("id", run_id).execute()
        except Exception:
            logger.warning(f"Could not update agent_runs {run_id}", exc_info=True)

    try:
        sb.table("tenant_agent_config").upsert({
            "tenant_id": tenant_id,
            "agent_name": agent_name,
            "last_run_at": started,
            "schedule": DEFAULT_SCHEDULES.get(agent_name, "daily"),
        }, on_conflict="tenant_id,agent_name").execute()
    except Exception:
        pass


# ── POST /agents/{agent_name}/trigger — Manually run an agent ────────────────

@router.post("/agents/{agent_name}/trigger")
async def trigger_agent(agent_name: str, request: Request):
    """
    Enqueue an agent run for this tenant.

    The actual agent cycle is dispatched as a background task so this endpoint
    returns immediately. Clients should poll GET /agent-runs to follow status.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    if agent_name not in ALL_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_name}")

    sb = get_supabase()

    run_id = None
    try:
        run_result = sb.table("agent_runs").insert({
            "tenant_id": tenant_id,
            "agent_name": agent_name,
            "status": "running",
        }).execute()
        if run_result.data:
            run_id = run_result.data[0]["id"]
    except Exception as e:
        logger.warning(f"Could not record agent run: {e}")

    asyncio.create_task(_execute_run(run_id, tenant_id, agent_name))

    return {
        "success": True,
        "agent": agent_name,
        "status": "running",
        "run_id": run_id,
    }


# ── GET /agent-runs — Recent run history ─────────────────────────────────────

@router.get("/agent-runs")
async def get_agent_runs(request: Request, limit: int = 10):
    """Return recent agent runs for this tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    try:
        result = (
            sb.table("agent_runs")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"runs": result.data or []}
    except Exception as e:
        logger.error(f"get_agent_runs error: {e}")
        return {"runs": []}


# ── GET /agent-runs/{run_id} — Single run status (used by dashboard polling) ──

@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str, request: Request):
    """Return a single run for status polling. 404 if not in this tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("agent_runs")
            .select("*")
            .eq("id", run_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Run not found")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_agent_run error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Watchdog: mark stale "running" rows as failed ────────────────────────────

async def reap_stale_runs() -> int:
    """
    Mark agent_runs rows that have been "running" for longer than STALE_RUN_AFTER
    as failed. Called on startup and periodically by the scheduler so a process
    crash mid-cycle doesn't leave the dashboard stuck on a spinner.
    Returns the number of rows updated.
    """
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - STALE_RUN_AFTER).isoformat()
    try:
        result = (
            sb.table("agent_runs")
            .update({
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": "Run did not complete within timeout",
            })
            .eq("status", "running")
            .lt("started_at", cutoff)
            .execute()
        )
        n = len(result.data or [])
        if n:
            logger.warning(f"Watchdog marked {n} stale agent_runs as failed")
        return n
    except Exception as e:
        logger.warning(f"reap_stale_runs failed: {e}")
        return 0
