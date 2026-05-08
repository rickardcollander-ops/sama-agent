"""
Content Plan Creator -- bridges analysis output to content plan.

Given a completed analysis_run, this module:
  1. Loads (or scrapes) the per-tenant brand voice.
  2. Ranks the analysis gaps + opportunities.
  3. Distributes the chosen number of articles per week across 30/60/90
     days based on gap priority (high -> first 30d, medium -> 31-60d,
     low -> 61-90d).
  4. Drafts each article tone-matched to the tenant's voice and runs the
     em-dash / AI-tell cleanup pass.
  5. For every chosen social platform, generates a platform-specific post
     with an {{ARTICLE_URL}} placeholder, scheduled for article+1 day,
     linked back to the parent via parent_content_id and
     parent_plan_item_id.

Nothing is shared across tenants -- tenant_id is mandatory and used as
the key for both the brand voice lookup and every DB write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from anthropic import Anthropic

from shared.config import settings
from shared.database import get_supabase
from .brand_voice import BrandVoice, BrandVoiceNotFoundError, TenantBrandVoice
from . import brand_voice_scraper
from .social_for_article import generate_for_article

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = {"linkedin", "x", "instagram", "facebook"}
DEFAULT_WEEKDAY = 1  # Tuesday (Mon=0)
MODEL = getattr(settings, "CLAUDE_MODEL", "claude-sonnet-4-6")


async def _ensure_voice(tenant_id: str, domain: str, brand_name: str) -> TenantBrandVoice:
    """Load BrandVoice.for_tenant; if missing, trigger scrape and try again.

    Falls back to the default voice when the tenant_brand_voices table is
    unavailable (e.g. migration 039 not yet applied) or when scraping fails
    for any reason. The plan should still produce articles in that case --
    the user gets reasonable copy instead of a 500.
    """
    try:
        return BrandVoice.for_tenant(tenant_id)
    except BrandVoiceNotFoundError:
        pass

    if not domain:
        logger.warning(
            "content_plan_creator: no voice row for tenant=%s and no domain to scrape; "
            "using default voice",
            tenant_id,
        )
        return BrandVoice.for_tenant("default")

    logger.info(f"content_plan_creator: scraping voice for tenant={tenant_id}")
    try:
        await brand_voice_scraper.scrape_and_extract(
            tenant_id=tenant_id,
            domain=domain,
            brand_name=brand_name,
        )
    except Exception as e:
        logger.warning(
            "content_plan_creator: brand voice scrape/persist failed for tenant=%s "
            "(%s); falling back to default voice",
            tenant_id, e,
        )
        return BrandVoice.for_tenant("default")

    try:
        return BrandVoice.for_tenant(tenant_id)
    except BrandVoiceNotFoundError:
        return BrandVoice.for_tenant("default")


def _load_tenant_brand_context(tenant_id: str) -> Dict[str, Any]:
    """Read brand_name + domain + competitors from user_sites/user_settings."""
    sb = get_supabase()
    try:
        site = (
            sb.table("user_sites")
            .select("settings")
            .eq("id", tenant_id)
            .single()
            .execute()
        )
        if site.data and isinstance(site.data.get("settings"), dict):
            return site.data["settings"]
    except Exception:
        pass
    try:
        legacy = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        if legacy.data:
            return legacy.data.get("settings", {}) or {}
    except Exception:
        pass
    return {}


def _bucket_for_gap(gap_type: str, ai_mention: bool) -> str:
    """Map analysis gap classification to a 30/60/90 bucket."""
    gt = (gap_type or "").lower()
    if gt in ("competitor_dominates", "both_losers") or not ai_mention:
        return "30"
    if gt == "seo_winner_geo_loser":
        return "60"
    return "90"


def _priority_for_bucket(bucket: str) -> str:
    return {"30": "high", "60": "medium", "90": "low"}.get(bucket, "medium")


def _extract_topics_from_run(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull (query, gap_type, ai_mention, competitors) tuples from an analysis_run payload."""
    topics: List[Dict[str, Any]] = []
    seen_queries = set()

    for qr in payload.get("query_results", []) or []:
        query = (qr.get("query") or "").strip()
        if not query or query.lower() in seen_queries:
            continue
        seen_queries.add(query.lower())

        gap_type = qr.get("gap") or ""
        ai_results = qr.get("ai_results") or []
        ai_mentions = sum(1 for r in ai_results if r.get("mentioned"))
        competitors_seen = []
        for r in ai_results:
            for c in r.get("competitors_mentioned") or []:
                if c and c not in competitors_seen:
                    competitors_seen.append(c)

        topics.append({
            "query": query,
            "gap_type": gap_type,
            "ai_mention": ai_mentions > 0,
            "seo_rank": qr.get("seo_rank"),
            "competitors": competitors_seen[:3],
        })

    # Add top_opportunities as fallback if query_results was sparse
    overview = payload.get("overview") or {}
    for opp in overview.get("top_opportunities", []) or []:
        q = (opp.get("query") or "").strip()
        if q and q.lower() not in seen_queries:
            topics.append({
                "query": q,
                "gap_type": "both_losers",
                "ai_mention": False,
                "seo_rank": None,
                "competitors": [],
            })
            seen_queries.add(q.lower())

    return topics


async def _generate_titles_for_topics(
    voice: TenantBrandVoice,
    brand_name: str,
    topics: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Ask Claude to turn each topic into a concrete article title + angle."""
    if not topics or not settings.ANTHROPIC_API_KEY:
        return []

    topic_lines = "\n".join(
        f"{i+1}. query=\"{t['query']}\" gap={t['gap_type']} ai_mention={t['ai_mention']} competitors={t['competitors']}"
        for i, t in enumerate(topics)
    )

    system_prompt = voice.get_system_prompt("blog", brand_name=brand_name)
    user_prompt = f"""Below is a list of search queries where we have a gap in SEO and/or AI visibility. For each, propose ONE concrete article that addresses the query in our brand voice.

Queries:
{topic_lines}

Return ONLY a JSON array of {len(topics)} objects, in the same order:
[
  {{
    "title": "Concrete article headline that targets the query",
    "angle": "One sentence describing the angle/POV",
    "target_keyword": "the primary keyword to optimise for",
    "pillar": "a one-word content pillar (churn|onboarding|automation|scaling|metrics|comparison|other)",
    "rationale": "why this matters (e.g. 'competitor X dominates AI answers for this query')"
  }}
]
"""
    from shared.llm import call_claude
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await call_claude(
        client=client,
        model=MODEL,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=2048,
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]

    try:
        ideas = json.loads(text.strip())
    except json.JSONDecodeError as e:
        logger.error(f"_generate_titles_for_topics: parse failed: {e}; raw: {text[:500]}")
        return []
    if not isinstance(ideas, list):
        return []

    return ideas[: len(topics)]


async def _draft_article(
    voice: TenantBrandVoice,
    brand_name: str,
    title: str,
    angle: str,
    target_keyword: str,
) -> Dict[str, Any]:
    """Ask Claude for a full article + meta. Returns dict with content/title/etc."""
    system_prompt = voice.get_system_prompt("blog", brand_name=brand_name)
    user_prompt = f"""Write a complete blog post.

Title: {title}
Angle: {angle}
Target keyword: {target_keyword}
Length: 1500-2200 words.

Return ONLY a JSON object (no markdown fences) with:
{{
  "title": "final title (may refine)",
  "content": "full markdown article",
  "meta_title": "<= 60 chars",
  "meta_description": "150-160 chars",
  "word_count": <integer>
}}
Reminder: NEVER use em-dashes. Use commas or periods instead.
"""
    from shared.llm import call_claude
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await call_claude(
        client=client,
        model=MODEL,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4096,
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]

    try:
        article = json.loads(text.strip())
    except json.JSONDecodeError:
        article = {"title": title, "content": text, "word_count": len(text.split())}

    # Em-dash cleanup pass
    if isinstance(article.get("content"), str):
        article["content"] = BrandVoice.cleanup_ai_tells(article["content"])
    if isinstance(article.get("title"), str):
        article["title"] = BrandVoice.cleanup_ai_tells(article["title"])

    return article


def _next_weekday(start: datetime, weekday: int) -> datetime:
    """Return the first datetime >= start whose weekday() == weekday."""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


def _schedule_dates(
    articles_per_week: int,
    bucket_counts: Dict[str, int],
    start: Optional[datetime] = None,
    weekday: int = DEFAULT_WEEKDAY,
) -> Dict[str, List[datetime]]:
    """Return {bucket -> [datetime,...]} with dates evenly spread within each window."""
    start = start or datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    base = _next_weekday(start, weekday)
    out: Dict[str, List[datetime]] = {"30": [], "60": [], "90": []}

    bucket_ranges = {
        "30": (0, 30),
        "60": (30, 60),
        "90": (60, 90),
    }

    for bucket, (lo, hi) in bucket_ranges.items():
        n = bucket_counts.get(bucket, 0)
        if n <= 0:
            continue
        # Days inside the window where weekday == target
        candidate_days = []
        d = base + timedelta(days=lo)
        while d < base + timedelta(days=hi):
            candidate_days.append(d)
            d += timedelta(days=7)
        # If we need more than one per week, also offer mid-week slots (Thursday)
        if articles_per_week >= 2:
            mid = base + timedelta(days=lo, hours=0)
            while mid < base + timedelta(days=hi):
                candidate_days.append(mid + timedelta(days=3))
                mid += timedelta(days=7)
        if articles_per_week >= 3:
            mid2 = base + timedelta(days=lo)
            while mid2 < base + timedelta(days=hi):
                candidate_days.append(mid2 + timedelta(days=5))
                mid2 += timedelta(days=7)
        candidate_days = sorted(set(candidate_days))[:n]
        out[bucket] = candidate_days

    return out


def _social_schedule_dates(
    posts_per_week: int,
    start: Optional[datetime] = None,
    weeks: int = 13,
) -> List[datetime]:
    """Return ``posts_per_week × weeks`` datetimes spread across 90 days.

    Slots within a week land on Mon, Wed, Fri, Tue, Thu, Sat, Sun (in that
    order) so 1/week defaults to Monday and 3/week ends up on Mon/Wed/Fri.
    """
    if posts_per_week <= 0 or weeks <= 0:
        return []
    start = start or datetime.now(timezone.utc).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    week_offsets = [0, 2, 4, 1, 3, 5, 6][: max(0, min(7, posts_per_week))]
    monday = start - timedelta(days=start.weekday())
    if monday < start:
        monday = monday + timedelta(days=7)
    out: List[datetime] = []
    for w in range(weeks):
        base = monday + timedelta(days=7 * w)
        for off in week_offsets:
            out.append(base + timedelta(days=off))
    return out


# PostgREST returns schema-cache misses with a recognisable message.
# Older prod schemas may not have all the columns the agent populates
# (e.g. migration 039's source_analysis_run_id), so we strip the offending
# column and retry instead of dropping the whole row.
_PGRST_MISSING_COL_RE = re.compile(
    r"Could not find the ['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]? column",
)


def _safe_insert(
    sb: Any, table: str, data: Dict[str, Any], max_retries: int = 8,
) -> Optional[Dict[str, Any]]:
    """Insert ``data`` into ``table``; on "unknown column" errors, drop the
    offending column and retry. Returns the first inserted row, or ``None``
    if the insert keeps failing for unrelated reasons.
    """
    payload = dict(data)
    for _ in range(max_retries):
        try:
            row = sb.table(table).insert(payload).execute()
            return (row.data or [{}])[0] or None
        except Exception as e:
            msg = str(e)
            match = _PGRST_MISSING_COL_RE.search(msg)
            if not match:
                logger.error(f"{table} insert failed: {e}")
                return None
            missing = match.group(1)
            if missing not in payload:
                logger.error(f"{table} insert failed (col {missing!r} not in payload): {e}")
                return None
            logger.warning(
                f"{table} insert: dropping unknown column {missing!r} and retrying"
            )
            payload.pop(missing, None)
    logger.error(f"{table} insert: gave up after {max_retries} retries")
    return None


def _nearest_article(
    articles: List[Dict[str, Any]], when: datetime
) -> Optional[Dict[str, Any]]:
    """Pick the article whose ``scheduled_for`` is closest to ``when``."""
    if not articles:
        return None
    return min(articles, key=lambda a: abs(a["scheduled_for"] - when))


async def create_plan_from_analysis(
    tenant_id: str,
    analysis_run_id: str,
    articles_per_week: int,
    social_platforms: List[str],
    social_posts_per_week: Optional[int] = None,
    analysis_payload: Optional[Dict[str, Any]] = None,
    analysis_domain: Optional[str] = None,
    analysis_brand_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Main entry point. Returns counts of created articles + social posts.

    The dashboard caches completed analysis runs locally
    (``user_settings.saved_analyses_by_tenant``) so history survives even when
    the agent backend rotates rows. When the user clicks "Skapa content-plan"
    on such a saved run, the ``analysis_run_id`` will not exist in the
    backend's ``analysis_runs`` table. To handle that, the caller may pass
    ``analysis_payload`` / ``analysis_domain`` / ``analysis_brand_name``
    inline; we use them directly and skip the DB lookup.
    """
    if not tenant_id or tenant_id == "default":
        raise ValueError("tenant_id is required and must not be 'default'")
    if articles_per_week < 1 or articles_per_week > 5:
        raise ValueError("articles_per_week must be 1-5")

    platforms = [p.lower().strip() for p in (social_platforms or []) if p]
    platforms = [p for p in platforms if p in SUPPORTED_PLATFORMS]

    if social_posts_per_week is None:
        social_per_week = articles_per_week
    else:
        social_per_week = max(0, min(7, int(social_posts_per_week)))

    sb = get_supabase()

    # 1. Resolve analysis run. Prefer an inline payload from the caller (set
    # when the dashboard is operating on a locally-cached run). Otherwise
    # look it up in the backend's analysis_runs table. We use limit(1)
    # instead of .single() so a missing row produces our friendly
    # RuntimeError rather than the raw PostgREST PGRST116 error bubbling up.
    if analysis_payload is not None:
        run = {
            "domain": analysis_domain or "",
            "brand_name": analysis_brand_name or "",
        }
        payload = analysis_payload or {}
    else:
        try:
            run_q = (
                sb.table("analysis_runs")
                .select("id,payload,domain,brand_name,tenant_id")
                .eq("id", analysis_run_id)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )
        except Exception as e:
            raise RuntimeError(
                f"Could not load analysis_run {analysis_run_id} for tenant {tenant_id}: {e}"
            ) from e
        rows = run_q.data or []
        if not rows:
            raise RuntimeError(
                f"analysis_run {analysis_run_id} not found for tenant {tenant_id}. "
                "Re-run the analysis or include the payload inline."
            )
        run = rows[0]
        payload = run.get("payload") or {}

    # 2. Brand context
    brand_ctx = _load_tenant_brand_context(tenant_id)
    domain = run.get("domain") or brand_ctx.get("domain") or ""
    brand_name = run.get("brand_name") or brand_ctx.get("brand_name") or tenant_id

    # 3. Voice for title generation only. We deliberately use the default
    # voice (no DB lookup, no scraping) so plan creation stays fast and
    # cheap. The fully-tone-matched draft happens later, in
    # _materialise_idea, when the user approves an individual idea.
    voice = BrandVoice.for_tenant("default")

    # 4. Extract + bucket topics
    topics = _extract_topics_from_run(payload)
    if not topics:
        return {
            "plan_id": analysis_run_id,
            "ideas_created": 0,
            "social_ideas_created": 0,
            "scheduled_through": None,
            "warning": "analysis_run had no actionable topics",
        }

    target_total = articles_per_week * 13  # ~13 weeks across 90 days
    # Bucket assignment
    bucketed: Dict[str, List[Dict[str, Any]]] = {"30": [], "60": [], "90": []}
    for t in topics:
        b = _bucket_for_gap(t["gap_type"], t["ai_mention"])
        bucketed[b].append(t)

    # Trim to target_total proportionally so the user gets exactly the
    # cadence they asked for.
    target_split = {
        "30": max(1, target_total // 3),
        "60": max(1, target_total // 3),
        "90": target_total - 2 * max(1, target_total // 3),
    }
    for b in ("30", "60", "90"):
        if len(bucketed[b]) > target_split[b]:
            bucketed[b] = bucketed[b][: target_split[b]]
        # If we're short, top up from later buckets
        while len(bucketed[b]) < target_split[b]:
            stolen = None
            for src in ("90", "60", "30"):
                if src != b and bucketed[src]:
                    stolen = bucketed[src].pop(0)
                    break
            if not stolen:
                break
            bucketed[b].append(stolen)

    bucket_counts = {b: len(bucketed[b]) for b in bucketed}
    schedule = _schedule_dates(articles_per_week, bucket_counts)

    # 5. Generate concrete titles in one batched Claude call. This is the
    # only LLM round-trip during plan creation -- the actual article body
    # is produced later, on user approval, by _materialise_idea.
    flat: List[Tuple[str, Dict[str, Any]]] = []
    for b in ("30", "60", "90"):
        for t in bucketed[b]:
            flat.append((b, t))
    titles = await _generate_titles_for_topics(
        voice, brand_name, [t for _, t in flat]
    )
    if len(titles) != len(flat):
        # If batched call returned fewer than expected, pad with stub titles
        while len(titles) < len(flat):
            t = flat[len(titles)][1]
            titles.append({
                "title": f"Article on {t['query']}",
                "angle": "",
                "target_keyword": t["query"],
                "pillar": "",
                "rationale": "",
            })

    # 6. For each (bucket, topic, idea, schedule_date): write a plan-item
    # row with status='idea'. No content_pieces row is created -- that
    # happens later in _materialise_idea. This keeps plan creation fast
    # (one Claude call total) and avoids burning tokens on ideas the user
    # might never approve.
    ideas_created = 0
    social_ideas_created = 0
    last_date: Optional[datetime] = None
    article_records: List[Dict[str, Any]] = []

    schedule_cursor: Dict[str, int] = {"30": 0, "60": 0, "90": 0}
    now_iso = datetime.now(timezone.utc).isoformat()

    for (bucket, topic), idea in zip(flat, titles):
        sched_list = schedule.get(bucket, [])
        idx = schedule_cursor[bucket]
        if idx >= len(sched_list):
            continue
        article_date = sched_list[idx]
        schedule_cursor[bucket] = idx + 1
        priority = _priority_for_bucket(bucket)

        rationale = idea.get("rationale") or ""
        target_keyword = idea.get("target_keyword") or topic["query"]
        title = (idea.get("title") or f"Article on {topic['query']}")[:300]
        pillar = (idea.get("pillar") or "")[:100]
        angle = idea.get("angle") or ""

        plan_data = {
            "tenant_id": tenant_id,
            "title": title,
            "topic": (rationale or angle)[:1000] or None,
            "content_type": "blog_article",
            "target_keyword": target_keyword[:200],
            "pillar": pillar or None,
            "reason": rationale[:500] or None,
            "priority": priority,
            "status": "idea",
            "source": "analysis_gap",
            "source_run_id": analysis_run_id,
            "content_piece_id": None,
            "scheduled_for": article_date.isoformat(),
            "auto_publish_on_schedule": False,
            "metadata": {
                "bucket_days": bucket,
                "gap_type": topic["gap_type"],
                "competitors": topic.get("competitors", []),
                "angle": angle,
            },
            "created_at": now_iso,
        }
        plan_row = _safe_insert(sb, "content_plan_items", plan_data)
        article_plan_id = plan_row.get("id") if plan_row else None
        if not article_plan_id:
            continue

        ideas_created += 1
        if article_date > (last_date or article_date):
            last_date = article_date
        elif last_date is None:
            last_date = article_date

        article_records.append({
            "plan_id": article_plan_id,
            "title": title,
            "target_keyword": target_keyword,
            "scheduled_for": article_date,
            "priority": priority,
        })

    # 7. Social plan items, one per (platform × social_date). Each one
    # links to the nearest article idea via parent_plan_item_id so when
    # the article is approved, _materialise_idea can cascade-draft the
    # social children with the right parent context.
    if article_records and platforms and social_per_week > 0:
        social_dates = _social_schedule_dates(social_per_week)
        for platform in platforms:
            for slot_idx, social_date in enumerate(social_dates):
                parent = _nearest_article(article_records, social_date) \
                    or article_records[slot_idx % len(article_records)]

                social_plan_data = {
                    "tenant_id": tenant_id,
                    "title": f"{platform.title()} post: {parent['title']}"[:300],
                    "topic": f"Social post linking to article: {parent['title']}"[:1000],
                    "content_type": f"social_{platform}",
                    "target_keyword": parent["target_keyword"][:200],
                    "priority": parent["priority"],
                    "status": "idea",
                    "source": "analysis_gap",
                    "source_run_id": analysis_run_id,
                    "content_piece_id": None,
                    "parent_plan_item_id": parent["plan_id"],
                    "scheduled_for": social_date.isoformat(),
                    "auto_publish_on_schedule": False,
                    "metadata": {
                        "platform": platform,
                        "parent_article_title": parent["title"],
                    },
                    "created_at": now_iso,
                }
                if _safe_insert(sb, "content_plan_items", social_plan_data) is not None:
                    social_ideas_created += 1
                    if social_date > (last_date or social_date):
                        last_date = social_date

    return {
        "plan_id": analysis_run_id,
        "ideas_created": ideas_created,
        "social_ideas_created": social_ideas_created,
        "scheduled_through": last_date.isoformat() if last_date else None,
    }
