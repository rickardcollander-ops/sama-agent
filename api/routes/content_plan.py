"""
Content Plan API
================

A persistent plan of content ideas. Each idea lives in
``content_plan_items`` with a status (idea → drafting → draft → published)
and an optional FK to a row in ``content_pieces`` once the idea has been
materialised into a full article.

Each row carries a ``source`` column so the dashboard can render one
unified "what to write next" list with filter chips:

* ``manual``         — user added by hand
* ``ai_generated``   — produced by /api/content/plan/generate
* ``analysis_gap``   — auto-fed from the OODA content analysis loop
* ``competitor_gap`` — auto-fed from competitor coverage analysis

Each row may also carry a ``scheduled_for`` timestamp + an
``auto_publish_on_schedule`` flag. The scheduler picks these up
hourly: when ``scheduled_for <= now()``, the idea is drafted, and if
the flag is set the resulting article is also published.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Models ───────────────────────────────────────────────────────────────────

class PlanItemCreate(BaseModel):
    title: str
    topic: Optional[str] = None
    content_type: str = "blog_article"
    target_keyword: Optional[str] = None
    pillar: Optional[str] = None
    reason: Optional[str] = None
    priority: str = "medium"
    status: str = "idea"
    source: str = "manual"
    # Optional link to an existing content_pieces row. When set, the calendar
    # treats this plan item as the schedule for that piece, so users can
    # promote a draft from /c/content onto the calendar without duplicating.
    content_piece_id: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    auto_publish_on_schedule: bool = False


class PlanItemUpdate(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None
    content_type: Optional[str] = None
    target_keyword: Optional[str] = None
    pillar: Optional[str] = None
    reason: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    content_piece_id: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    auto_publish_on_schedule: Optional[bool] = None


class PlanGenerateRequest(BaseModel):
    count: int = 6
    brand_name: Optional[str] = None
    domain: Optional[str] = None
    brand_description: Optional[str] = None
    target_audience: Optional[str] = None
    competitors: List[str] = []
    pillar: Optional[str] = None
    replace: bool = False


class CalendarItemCreate(BaseModel):
    """Quick-add from the calendar UI: schedule + maybe draft now."""
    title: str
    scheduled_for: datetime
    content_type: str = "blog_article"
    target_keyword: Optional[str] = None
    topic: Optional[str] = None
    pillar: Optional[str] = None
    priority: str = "medium"
    auto_publish_on_schedule: bool = False
    draft_now: bool = False  # if True, materialise the article immediately


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_brand_context(tenant_id: str) -> dict:
    if not tenant_id or tenant_id == "default":
        return {}
    try:
        sb = get_supabase()
        data = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        return data.data.get("settings", {}) if data.data else {}
    except Exception:
        return {}


def _row(item: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "title": item.get("title") or "",
        "topic": item.get("topic") or "",
        "content_type": item.get("content_type") or "blog_article",
        "target_keyword": item.get("target_keyword") or "",
        "pillar": item.get("pillar") or "",
        "reason": item.get("reason") or "",
        "priority": item.get("priority") or "medium",
        "status": item.get("status") or "idea",
        "source": item.get("source") or "manual",
        "content_piece_id": item.get("content_piece_id"),
        "scheduled_for": item.get("scheduled_for"),
        "auto_publish_on_schedule": bool(item.get("auto_publish_on_schedule") or False),
        "metadata": item.get("metadata") or {},
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def upsert_analysis_gap_items(
    tenant_id: str,
    actions: List[Dict[str, Any]],
    cycle_id: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """Insert plan rows for analysis gap actions, skipping duplicates by keyword.

    When ``source`` is provided it overrides the auto-detected source label
    (e.g. ``"ai_visibility_gap"`` for GEO-derived gaps). Otherwise we infer
    ``competitor_gap`` for actions tagged with a competitor and fall back to
    ``analysis_gap``.
    """
    if not actions:
        return 0

    sb = get_supabase()

    try:
        existing = (
            sb.table("content_plan_items")
            .select("target_keyword,content_piece_id")
            .eq("tenant_id", tenant_id)
            .execute()
        )
        existing_keywords = {
            (r.get("target_keyword") or "").strip().lower()
            for r in (existing.data or [])
            if r.get("target_keyword")
        }
    except Exception as e:
        logger.debug(f"upsert_analysis_gap_items: existing fetch failed: {e}")
        existing_keywords = set()

    rows: List[Dict[str, Any]] = []
    for action in actions:
        atype = action.get("type") or action.get("action_type") or ""
        if atype not in {"blog_post", "blog_article", "comparison"}:
            continue

        kw = (action.get("keyword") or "").strip()
        if kw and kw.lower() in existing_keywords:
            continue

        is_competitor = bool(action.get("competitor"))
        row_source = source or ("competitor_gap" if is_competitor else "analysis_gap")

        rows.append({
            "tenant_id": tenant_id,
            "title": str(action.get("title") or f"Cover keyword '{kw}'")[:300],
            "topic": str(action.get("description") or "")[:1000],
            "content_type": "blog_article" if atype != "comparison" else "comparison",
            "target_keyword": kw[:200] or None,
            "priority": str(action.get("priority") or "medium"),
            "reason": str(action.get("action") or action.get("description") or "")[:500],
            "status": "idea",
            "source": row_source,
            "source_run_id": cycle_id,
            "metadata": {
                "competitor": action.get("competitor"),
                "pillar": action.get("pillar"),
                "from_action_id": action.get("id"),
            },
        })
        if kw:
            existing_keywords.add(kw.lower())

    if not rows:
        return 0

    inserted = 0
    for r in rows:
        try:
            sb.table("content_plan_items").insert(r).execute()
            inserted += 1
        except Exception as e:
            logger.debug(f"upsert_analysis_gap_items skipped row: {e}")
    return inserted


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("/plan")
async def list_plan(
    request: Request,
    status: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 200,
):
    """List content plan items for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        q = (
            sb.table("content_plan_items")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            q = q.eq("status", status)
        if source:
            q = q.eq("source", source)
        result = q.execute()
        return {"items": [_row(x) for x in (result.data or [])]}
    except Exception as e:
        logger.error(f"list_plan error: {e}")
        return {"items": [], "error": str(e)}


@router.get("/plan/calendar")
async def list_plan_for_calendar(request: Request, start: str, end: str):
    """Return all plan items + content pieces scheduled in [start, end].

    Powers the calendar month grid. The dashboard sends ISO dates for the
    visible window; we return both rows that have a scheduled_for inside
    the window AND content_pieces that were created/published inside the
    window (so finished work shows up too).
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()

        scheduled = (
            sb.table("content_plan_items")
            .select("*")
            .eq("tenant_id", tenant_id)
            .gte("scheduled_for", start)
            .lte("scheduled_for", end)
            .execute()
        )
        pieces_published = (
            sb.table("content_pieces")
            .select("id,title,content_type,status,published_at,target_url,external_url,created_at")
            .eq("tenant_id", tenant_id)
            .eq("status", "published")
            .gte("published_at", start)
            .lte("published_at", end)
            .execute()
        )
        return {
            "scheduled": [_row(x) for x in (scheduled.data or [])],
            "published_pieces": pieces_published.data or [],
        }
    except Exception as e:
        logger.error(f"list_plan_for_calendar error: {e}")
        return {"scheduled": [], "published_pieces": [], "error": str(e)}


# ── Create one ───────────────────────────────────────────────────────────────

@router.post("/plan")
async def create_plan_item(request: Request, payload: PlanItemCreate):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        data = {
            **payload.model_dump(exclude_none=True, mode="json"),
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = sb.table("content_plan_items").insert(data).execute()
        return {"success": True, "item": _row(result.data[0]) if result.data else data}
    except Exception as e:
        logger.error(f"create_plan_item error: {e}")
        return {"success": False, "error": str(e)}


# ── Calendar quick-add: schedule + (optionally) draft right away ─────────────

@router.post("/plan/calendar")
async def calendar_create(request: Request, payload: CalendarItemCreate):
    """Add a row to the plan from the calendar UI.

    Always sets scheduled_for. If draft_now=true, immediately runs the
    drafting flow (same as POST /plan/:id/draft) so the editor opens with
    a complete article. The scheduler still owns the publish step on the
    target date — unless auto_publish_on_schedule=false, in which case it
    stays as a draft and the user clicks Approve & Publish manually.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()

    plan_data = {
        "tenant_id": tenant_id,
        "title": payload.title[:300],
        "topic": (payload.topic or "")[:1000] or None,
        "content_type": payload.content_type,
        "target_keyword": (payload.target_keyword or "")[:200] or None,
        "pillar": (payload.pillar or "")[:100] or None,
        "priority": payload.priority,
        "status": "idea",
        "source": "manual",
        "scheduled_for": payload.scheduled_for.isoformat() if payload.scheduled_for else None,
        "auto_publish_on_schedule": bool(payload.auto_publish_on_schedule),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = sb.table("content_plan_items").insert(plan_data).execute()
        item = (result.data or [{}])[0]
        item_id = item.get("id")
    except Exception as e:
        logger.error(f"calendar_create insert failed: {e}")
        return {"success": False, "error": str(e)}

    if not payload.draft_now:
        return {"success": True, "item": _row(item)}

    # Draft inline so the editor opens with a real article.
    try:
        draft_result = await _materialise_idea(sb, tenant_id, item)
        return {"success": True, "item": _row(item), **draft_result}
    except Exception as e:
        logger.error(f"calendar_create draft_now failed: {e}")
        return {"success": True, "item": _row(item), "draft_error": str(e)}


# ── Update ───────────────────────────────────────────────────────────────────

@router.patch("/plan/{item_id}")
async def update_plan_item(item_id: str, payload: PlanItemUpdate, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        update_data = {k: v for k, v in payload.model_dump(mode="json").items() if v is not None}
        if not update_data:
            return {"success": True, "message": "Nothing to update"}
        result = (
            sb.table("content_plan_items")
            .update(update_data)
            .eq("id", item_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        if result.data:
            return {"success": True, "item": _row(result.data[0])}
        return {"success": True}
    except Exception as e:
        logger.error(f"update_plan_item error: {e}")
        return {"success": False, "error": str(e)}


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/plan/{item_id}")
async def delete_plan_item(item_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        sb.table("content_plan_items").delete().eq("id", item_id).eq("tenant_id", tenant_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"delete_plan_item error: {e}")
        return {"success": False, "error": str(e)}


# ── Bulk schedule + per-item cadence ─────────────────────────────────────────

class BulkScheduleEntry(BaseModel):
    item_id: str
    scheduled_for: datetime
    auto_publish_on_schedule: bool = False


class BulkScheduleRequest(BaseModel):
    entries: List[BulkScheduleEntry]
    # item_id -> {"count": N, "interval_days": D}
    repeats: Optional[Dict[str, Dict[str, int]]] = None


@router.post("/plan/bulk-schedule")
async def bulk_schedule(request: Request, payload: BulkScheduleRequest):
    """Apply scheduled_for to multiple plan items in one round-trip.

    For items listed in ``repeats``, also clones each row N-1 times at
    ``interval_days`` apart (e.g. weekly LinkedIn series). Repeated rows
    drop ``target_keyword`` to bypass the keyword dedupe constraint.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    if not payload.entries:
        return {"success": True, "items": [], "count": 0}

    try:
        from datetime import timedelta
        sb = get_supabase()
        now_iso = datetime.now(timezone.utc).isoformat()
        affected: List[Dict[str, Any]] = []

        for entry in payload.entries:
            try:
                upd = (
                    sb.table("content_plan_items")
                    .update({
                        "scheduled_for": entry.scheduled_for.isoformat(),
                        "auto_publish_on_schedule": entry.auto_publish_on_schedule,
                        "status": "scheduled",
                        "updated_at": now_iso,
                    })
                    .eq("id", entry.item_id)
                    .eq("tenant_id", tenant_id)
                    .execute()
                )
            except Exception as e:
                logger.debug(f"bulk_schedule update {entry.item_id}: {e}")
                continue
            if not upd.data:
                continue
            affected.extend(upd.data)

            repeats = (payload.repeats or {}).get(entry.item_id) or {}
            count = int(repeats.get("count") or 1)
            interval = int(repeats.get("interval_days") or 7)
            if count <= 1 or interval <= 0:
                continue

            original = upd.data[0]
            for n in range(1, count):
                sched = entry.scheduled_for + timedelta(days=interval * n)
                row = {
                    "tenant_id": tenant_id,
                    "source": original.get("source", "manual"),
                    "source_run_id": original.get("source_run_id"),
                    "title": original.get("title"),
                    "topic": original.get("topic"),
                    "content_type": original.get("content_type", "blog_article"),
                    # Drop keyword on repeats so the dedupe index doesn't fire.
                    "target_keyword": None,
                    "pillar": original.get("pillar"),
                    "reason": original.get("reason"),
                    "priority": original.get("priority"),
                    "status": "scheduled",
                    "scheduled_for": sched.isoformat(),
                    "auto_publish_on_schedule": entry.auto_publish_on_schedule,
                    "metadata": {
                        "repeat_of": entry.item_id,
                        "repeat_n": n,
                        "repeat_count": count,
                    },
                }
                try:
                    r = sb.table("content_plan_items").insert(row).execute()
                    if r.data:
                        affected.extend(r.data)
                except Exception as e:
                    logger.debug(f"bulk_schedule repeat n={n} skipped: {e}")

        return {"success": True, "items": affected, "count": len(affected)}
    except Exception as e:
        logger.error(f"bulk_schedule error: {e}")
        return {"success": False, "error": str(e)}


# ── AI batch generation ──────────────────────────────────────────────────────

@router.post("/plan/generate")
async def generate_plan(request: Request, payload: PlanGenerateRequest):
    """Use Claude to generate N content ideas and persist them as plan items."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    s = _load_brand_context(tenant_id)
    brand_name = payload.brand_name or s.get("brand_name", "")
    domain = payload.domain or s.get("domain", "")
    brand_description = payload.brand_description or s.get("brand_description", "")
    target_audience = payload.target_audience or s.get("target_audience", "")
    competitors = payload.competitors or s.get("competitors", []) or []

    count = max(1, min(payload.count, 12))

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        pillar_hint = f"\nFocus pillar: {payload.pillar}" if payload.pillar else ""
        prompt = f"""You are a B2B SaaS content strategist. Generate {count} content ideas
the team can publish over the next quarter.

Brand: {brand_name}
Website: {domain}
Description: {brand_description}
Target audience: {target_audience}
Competitors: {', '.join(competitors) if competitors else 'N/A'}{pillar_hint}

Mix the formats roughly: 60% blog_article, 25% linkedin_post, 15% email.

Return ONLY a JSON array (no markdown, no code fences) of {count} objects:
[
  {{
    "title": "Concrete article headline",
    "topic": "What the article is about, one sentence",
    "content_type": "blog_article|linkedin_post|email|comparison",
    "target_keyword": "primary SEO keyword",
    "pillar": "churn_prevention|health_scoring|cs_automation|onboarding|nrr_growth|competitor",
    "priority": "high|medium|low",
    "reason": "Why this matters now, one sentence"
  }}
]
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()

        try:
            ideas = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                fenced = text.split("```")[1]
                if fenced.startswith("json"):
                    fenced = fenced[4:]
                ideas = json.loads(fenced.strip())
            else:
                ideas = []

        if not isinstance(ideas, list):
            return {"success": False, "error": "AI returned non-list payload", "items": []}

        sb = get_supabase()

        if payload.replace:
            try:
                sb.table("content_plan_items").update({"status": "archived"}).eq(
                    "tenant_id", tenant_id
                ).eq("status", "idea").execute()
            except Exception as e:
                logger.debug(f"Failed to archive existing ideas: {e}")

        try:
            existing = (
                sb.table("content_plan_items")
                .select("target_keyword")
                .eq("tenant_id", tenant_id)
                .execute()
            )
            existing_keywords = {
                (r.get("target_keyword") or "").strip().lower()
                for r in (existing.data or [])
                if r.get("target_keyword")
            }
        except Exception:
            existing_keywords = set()

        rows = []
        skipped = 0
        for raw in ideas:
            if not isinstance(raw, dict):
                continue
            kw = str(raw.get("target_keyword") or "").strip()
            if kw and kw.lower() in existing_keywords:
                skipped += 1
                continue
            rows.append({
                "tenant_id": tenant_id,
                "title": str(raw.get("title") or "Untitled idea")[:300],
                "topic": str(raw.get("topic") or "")[:1000],
                "content_type": str(raw.get("content_type") or "blog_article"),
                "target_keyword": kw[:200] or None,
                "pillar": str(raw.get("pillar") or "")[:100],
                "priority": str(raw.get("priority") or "medium"),
                "reason": str(raw.get("reason") or "")[:500],
                "status": "idea",
                "source": "ai_generated",
                "metadata": {"generator": "claude", "model": settings.CLAUDE_MODEL},
            })
            if kw:
                existing_keywords.add(kw.lower())

        if not rows:
            return {"success": False, "error": "No new ideas (all keywords already in plan)", "items": [], "skipped": skipped}

        result = sb.table("content_plan_items").insert(rows).execute()
        return {
            "success": True,
            "items": [_row(x) for x in (result.data or [])],
            "skipped": skipped,
        }
    except Exception as e:
        logger.error(f"generate_plan error: {e}")
        return {"success": False, "error": str(e), "items": []}


# ── Materialiser (shared between /plan/:id/draft and calendar/scheduler) ─────

async def _materialise_idea(sb, tenant_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """Build a content_piece from a plan idea via Claude. Returns piece info."""
    item_id = item["id"]
    sb.table("content_plan_items").update({"status": "drafting"}).eq("id", item_id).execute()

    import anthropic
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    ctype = item.get("content_type") or "blog_article"
    target_words = (
        "1500-2200 words" if ctype == "blog_article" else
        "120-180 words" if ctype == "linkedin_post" else
        "300-500 words"
    )
    prompt = f"""You are an expert B2B SaaS marketer. Write a {ctype.replace('_', ' ')}.

Title: {item.get('title') or ''}
Topic: {item.get('topic') or ''}
Target keyword: {item.get('target_keyword') or ''}
Pillar: {item.get('pillar') or ''}
Length: {target_words}.

Return ONLY a JSON object (no markdown fences):
{{
  "title": "Final title (may refine the input)",
  "content": "Full article in markdown",
  "meta_title": "<= 60 chars",
  "meta_description": "150-160 chars",
  "word_count": <number>
}}
"""
    message = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    try:
        article = json.loads(text)
    except json.JSONDecodeError:
        if "```" in text:
            fenced = text.split("```")[1]
            if fenced.startswith("json"):
                fenced = fenced[4:]
            article = json.loads(fenced.strip())
        else:
            article = {"title": item.get("title"), "content": text, "word_count": len(text.split())}

    piece_data = {
        "tenant_id": tenant_id,
        "title": article.get("title") or item.get("title") or "Untitled",
        "content": article.get("content") or "",
        "content_type": ctype,
        "meta_title": article.get("meta_title") or "",
        "meta_description": article.get("meta_description") or "",
        "target_keyword": item.get("target_keyword") or "",
        "word_count": int(article.get("word_count") or len((article.get("content") or "").split())),
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    piece_result = sb.table("content_pieces").insert(piece_data).execute()
    piece = (piece_result.data or [{}])[0]
    piece_id = piece.get("id")

    sb.table("content_plan_items").update({
        "status": "draft",
        "content_piece_id": piece_id,
    }).eq("id", item_id).execute()

    return {
        "plan_item_id": item_id,
        "content_piece_id": piece_id,
        "piece": piece,
    }


# ── Materialise an idea into a content_piece (drafted full article) ──────────

@router.post("/plan/{item_id}/draft")
async def draft_plan_item(item_id: str, request: Request):
    """Turn a plan item into a full article and link them."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    sb = get_supabase()
    try:
        result = (
            sb.table("content_plan_items")
            .select("*")
            .eq("id", item_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Plan item not found")
        item = rows[0]

        result_data = await _materialise_idea(sb, tenant_id, item)
        return {"success": True, **result_data}
    except HTTPException:
        try:
            sb.table("content_plan_items").update({"status": "idea"}).eq("id", item_id).execute()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"draft_plan_item error: {e}")
        try:
            sb.table("content_plan_items").update({"status": "idea"}).eq("id", item_id).execute()
        except Exception:
            pass
        return {"success": False, "error": str(e)}


# ── Lineage: idea → draft → published ────────────────────────────────────────

@router.get("/pieces/{piece_id}/lineage")
async def get_piece_lineage(piece_id: str, request: Request):
    """Return the plan idea (if any) that produced this piece + status timeline."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    try:
        sb = get_supabase()
        plan_q = (
            sb.table("content_plan_items")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("content_piece_id", piece_id)
            .limit(1)
            .execute()
        )
        plan_rows = plan_q.data or []
        plan_item = _row(plan_rows[0]) if plan_rows else None

        piece_q = (
            sb.table("content_pieces")
            .select("status,created_at,target_url,title")
            .eq("id", piece_id)
            .limit(1)
            .execute()
        )
        piece_rows = piece_q.data or []
        piece = piece_rows[0] if piece_rows else None

        return {"plan_item": plan_item, "piece": piece}
    except Exception as e:
        logger.error(f"get_piece_lineage error: {e}")
        return {"plan_item": None, "piece": None, "error": str(e)}


# ── Scheduler hook: process due scheduled items ──────────────────────────────

async def process_due_scheduled_items() -> Dict[str, int]:
    """Find plan items whose scheduled_for has passed; draft (and optionally
    publish) them. Called from shared.scheduler hourly.
    """
    sb = get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()
    stats = {"drafted": 0, "published": 0, "failed": 0}

    try:
        result = (
            sb.table("content_plan_items")
            .select("*")
            .lte("scheduled_for", now_iso)
            .in_("status", ["idea"])
            .limit(50)
            .execute()
        )
        due = result.data or []
    except Exception as e:
        logger.error(f"process_due_scheduled_items: query failed: {e}")
        return stats

    for item in due:
        tenant_id = item.get("tenant_id") or "default"
        try:
            mat = await _materialise_idea(sb, tenant_id, item)
            stats["drafted"] += 1

            if not item.get("auto_publish_on_schedule"):
                continue

            piece_id = mat.get("content_piece_id")
            if not piece_id:
                continue

            # Publish via the canonical content_validation flow.
            try:
                from api.routes.content_validation import _publish_via_github
                gh = await _publish_via_github(mat["piece"])
                if gh.get("success"):
                    sb.table("content_pieces").update({
                        "status": "published",
                        "published_at": now_iso,
                        "external_url": gh.get("url"),
                        "target_url": gh.get("url"),
                    }).eq("id", piece_id).execute()
                    sb.table("content_plan_items").update({"status": "published"}).eq(
                        "id", item["id"]
                    ).execute()
                    stats["published"] += 1
            except Exception as e:
                logger.warning(f"scheduled publish for piece {piece_id} failed: {e}")
        except Exception as e:
            logger.warning(f"scheduled draft for item {item.get('id')} failed: {e}")
            stats["failed"] += 1
            try:
                sb.table("content_plan_items").update({"status": "idea"}).eq("id", item["id"]).execute()
            except Exception:
                pass

    return stats
