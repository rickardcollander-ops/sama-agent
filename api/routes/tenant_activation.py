"""
Tenant Activation & Agent Control API Routes
Activate tenants, manage agent configs, trigger manual runs, view run history.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase
from shared.usage import UsageLimitExceeded, check_and_increment

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


# ── POST /agents/{agent_name}/trigger — Manually run an agent ────────────────

@router.post("/agents/{agent_name}/trigger")
async def trigger_agent(agent_name: str, request: Request):
    """Manually trigger an agent run for this tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    if agent_name not in ALL_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_name}")

    try:
        await check_and_increment(tenant_id, "agent_runs")
    except UsageLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "message": str(e),
                "metric": e.metric,
                "limit": e.limit,
                "current": e.current,
            },
        )

    sb = get_supabase()

    # Record run start
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

    summary = ""
    error_msg = None
    status = "completed"

    try:
        # Use tenant agent factory where available
        if agent_name == "seo":
            from shared.tenant_agents import get_seo_agent
            agent = await get_seo_agent(tenant_id)
            result = await agent.run_cycle()
            summary = f"SEO cycle completed: {result}" if result else "SEO cycle completed"
        elif agent_name == "content":
            from shared.tenant_agents import get_content_agent
            agent = await get_content_agent(tenant_id)
            result = await agent.run_cycle()
            summary = f"Content cycle completed: {result}" if result else "Content cycle completed"
        elif agent_name == "social":
            from shared.tenant_agents import get_social_agent
            agent = await get_social_agent(tenant_id)
            result = await agent.run_cycle()
            summary = f"Social cycle completed: {result}" if result else "Social cycle completed"
        elif agent_name == "reviews":
            from shared.tenant_agents import get_review_agent
            agent = await get_review_agent(tenant_id)
            result = await agent.run_cycle()
            summary = f"Reviews cycle completed: {result}" if result else "Reviews cycle completed"
        elif agent_name == "analytics":
            from shared.tenant_agents import get_analytics_agent
            agent = await get_analytics_agent(tenant_id)
            result = await agent.run_cycle()
            summary = f"Analytics cycle completed: {result}" if result else "Analytics cycle completed"
        elif agent_name == "geo":
            summary = "GEO monitoring triggered"
        elif agent_name == "ads":
            summary = "Ads agent triggered"
        else:
            summary = f"{agent_name} triggered"
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        logger.error(f"Agent {agent_name} run failed for {tenant_id}: {e}")

    # Update run record
    if run_id:
        try:
            update_data = {
                "status": status,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
            }
            if error_msg:
                update_data["error"] = error_msg
            sb.table("agent_runs").update(update_data).eq("id", run_id).execute()
        except Exception:
            pass

    # Update last_run_at on config
    try:
        sb.table("tenant_agent_config").upsert({
            "tenant_id": tenant_id,
            "agent_name": agent_name,
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "schedule": DEFAULT_SCHEDULES.get(agent_name, "daily"),
        }, on_conflict="tenant_id,agent_name").execute()
    except Exception:
        pass

    if status == "failed":
        raise HTTPException(status_code=500, detail=error_msg or "Agent run failed")

    return {"success": True, "agent": agent_name, "status": status, "summary": summary}


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
