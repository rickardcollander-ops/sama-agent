"""
SEO Agent /analyze endpoint — full OODA loop
OBSERVE → ORIENT → DECIDE (strategy-driven) → ACT (via /execute)

Flow:
1. Observe: GSC data, keyword rankings, technical checks, CWV
2. Orient: Analyse gaps, trends, opportunities
3. Decide: Generate/load strategy → convert to prioritised agent_actions
4. Content actions are queued for Content Agent to execute
"""

import hashlib
import logging
from typing import Dict, Any, List

from shared.ooda_loop import OODALoop
from agents.seo import seo_agent
from shared.database import get_supabase

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _kw_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]


async def _ensure_strategy(keywords: list, sb, known_pages: dict = None) -> dict:
    """Load existing strategy or generate a fresh one if fingerprint changed."""
    from api.routes.seo import _build_fingerprint, _strategy_to_tasks
    from anthropic import Anthropic
    from shared.config import settings
    import json, re

    current_fp = _build_fingerprint(keywords)

    existing = sb.table("seo_strategies").select("*").order("created_at", desc=True).limit(1).execute()
    row = (existing.data or [None])[0]
    if row and row.get("data_fingerprint") == current_fp:
        logger.info("✅ Strategy up-to-date (fingerprint match)")
        return {"strategy": row["strategy_json"], "tasks": row["tasks"], "cached": True, "id": row["id"]}

    logger.info("🧠 Generating new SEO strategy (data changed)…")

    ranked   = [k for k in keywords if k.get("current_position") and k["current_position"] > 0]
    unranked = [k for k in keywords if not k.get("current_position")]
    top3     = [k for k in ranked if k["current_position"] <= 3]
    top10    = [k for k in ranked if k["current_position"] <= 10]
    page2    = [k for k in ranked if 11 <= k["current_position"] <= 20]

    audit_result = sb.table("seo_audits").select("*").order("audit_date", desc=True).limit(1).execute()
    latest_audit = (audit_result.data or [None])[0]
    audit_summary = ""
    if latest_audit:
        audit_summary = f"""
Latest Audit:
- Critical: {len(latest_audit.get('critical_issues') or [])} | High: {len(latest_audit.get('high_issues') or [])}
- LCP: {latest_audit.get('lcp_score', 'N/A')}ms, CLS: {latest_audit.get('cls_score', 'N/A')}"""

    kw_lines = "\n".join([
        f"- '{k['keyword']}' pos={k['current_position']} clicks={k.get('current_clicks',0)} "
        f"impressions={k.get('current_impressions',0)} intent={k.get('intent','')} priority={k.get('priority','')}"
        for k in sorted(ranked, key=lambda x: x["current_position"])
    ]) or "No ranked keywords yet"

    unranked_summary = ", ".join([f"'{k['keyword']}'" for k in unranked[:12]])

    # Build live pages context so Claude knows what already exists
    live_pages_info = ""
    if known_pages:
        live_list = known_pages.get("live_list", [])
        if live_list:
            live_pages_info = f"\n\nEXISTING PAGES ON SITE ({len(live_list)} pages):\n"
            live_pages_info += "\n".join([f"  - {p}" for p in live_list])
            live_pages_info += "\n\nIMPORTANT: Do NOT suggest creating pages that already exist above. "
            live_pages_info += "Focus on NEW content that doesn't exist yet."

    # Build existing content context
    content_info = ""
    try:
        content_result = sb.table("content").select("title, url_path, status, content_type").execute()
        content_rows = content_result.data or []
        if content_rows:
            content_info = f"\n\nEXISTING CONTENT IN DATABASE ({len(content_rows)} pieces):\n"
            for c in content_rows[:15]:
                content_info += f"  - [{c.get('content_type', '?')}] {c.get('title', '?')} → {c.get('url_path', 'no url')} ({c.get('status', '?')})\n"
            content_info += "\nDo NOT suggest creating content that already exists above."
    except Exception:
        pass

    prompt = f"""You are an expert SEO strategist for successifier.com (AI customer success platform).

CURRENT DATA:
Keywords tracked: {len(keywords)} | Ranked: {len(ranked)} | Top-3: {len(top3)} | Top-10: {len(top10)} | Page-2: {len(page2)}

Ranked keywords:
{kw_lines}

Unranked (no GSC data yet, these need content): {unranked_summary}
{audit_summary}{live_pages_info}{content_info}

Generate a concrete 90-day SEO strategy. Each action must be a single checkable task.
Only suggest creating NEW pages/content that don't already exist on the site.
Return ONLY valid JSON (no markdown, no explanation):
{{
  "headline": "one-sentence strategic focus",
  "quick_wins": [
    {{"title": "Specific task title", "action": "Exactly what to do", "impact": "high|medium", "effort": "low|medium|high", "timeframe": "1-2 weeks", "content_type": "blog|landing_page|comparison|on_page|technical|null"}}
  ],
  "month1": [{{"focus": "Theme", "actions": ["Specific task 1", "Specific task 2", "Specific task 3"]}}],
  "month2": [{{"focus": "Theme", "actions": ["Specific task 1", "Specific task 2", "Specific task 3"]}}],
  "month3": [{{"focus": "Theme", "actions": ["Specific task 1", "Specific task 2", "Specific task 3"]}}],
  "content_gaps": ["keyword or topic — each should become a NEW blog post or landing page that doesn't exist yet"],
  "technical_priorities": ["Concrete technical fix"],
  "kpi_targets": {{"top10_keywords": 5, "monthly_clicks": 200, "avg_position": 15}}
}}"""

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    match = re.search(r'\{[\s\S]*\}', text)
    strategy = json.loads(match.group()) if match else {
        "headline": "Build topical authority in customer success",
        "quick_wins": [], "month1": [], "month2": [], "month3": [],
        "content_gaps": [], "technical_priorities": [],
        "kpi_targets": {"top10_keywords": 5, "monthly_clicks": 200, "avg_position": 15}
    }

    tasks = _strategy_to_tasks(strategy)

    row = sb.table("seo_strategies").insert({
        "headline": strategy.get("headline", ""),
        "strategy_json": strategy,
        "tasks": tasks,
        "data_fingerprint": current_fp,
        "ranked_keywords_count": len(ranked),
        "total_keywords_count": len(keywords),
    }).execute()
    saved_id = (row.data or [{}])[0].get("id")

    return {"strategy": strategy, "tasks": tasks, "cached": False, "id": saved_id}


def _strategy_to_agent_actions(strategy: dict, keywords: list, tech_issues: list, cwv: dict) -> list:
    """
    Convert strategy + live data into a prioritised list of agent_actions.
    Content gaps → content actions for Content Agent.
    Technical → technical actions.
    Quick wins + monthly tasks → appropriate action types.
    """
    actions = []

    # ── 1. Content gaps from strategy → blog/landing page actions ──────────────
    for gap in strategy.get("content_gaps", [])[:10]:
        actions.append({
            "id": f"content-gap-{_kw_hash(gap)}",
            "type": "content",
            "priority": "high",
            "title": f"Create content for: '{gap}'",
            "description": f"Strategy identifies '{gap}' as a content gap. "
                           f"Create a targeted blog post or landing page.",
            "action": f"Generate SEO-optimised content targeting '{gap}'",
            "keyword": gap,
            "content_brief": {
                "content_type": "blog_post",
                "target_keyword": gap,
                "word_count": 1500,
                "source": "strategy_content_gap"
            },
            "expected_outcome": {"type": "ranking_improvement", "target_position": 10},
        })

    # ── 2. Quick wins from strategy ─────────────────────────────────────────────
    for win in strategy.get("quick_wins", []):
        content_type = win.get("content_type")
        action_type = "content" if content_type in ("blog", "landing_page", "comparison") else \
                      "technical" if content_type == "technical" else "on_page"
        actions.append({
            "id": f"quickwin-{_kw_hash(win.get('title', ''))}",
            "type": action_type,
            "priority": "high" if win.get("impact") == "high" else "medium",
            "title": win.get("title", ""),
            "description": win.get("action", ""),
            "action": win.get("action", ""),
            "keyword": "",
            "content_brief": {"content_type": content_type, "source": "strategy_quick_win"} if content_type else None,
            "expected_outcome": {"type": "quick_win", "timeframe": win.get("timeframe", "")},
        })

    # ── 3. Month-1 actions (most urgent) ────────────────────────────────────────
    for block in strategy.get("month1", []):
        for act in block.get("actions", []):
            actions.append({
                "id": f"m1-{_kw_hash(act)}",
                "type": "on_page",
                "priority": "high",
                "title": act,
                "description": f"Month 1 focus: {block.get('focus', '')}",
                "action": act,
                "keyword": "",
                "expected_outcome": {"type": "month1_task"},
            })

    # ── 4. Technical priorities from strategy ────────────────────────────────────
    for pri in strategy.get("technical_priorities", []):
        actions.append({
            "id": f"tech-strategy-{_kw_hash(pri)}",
            "type": "technical",
            "priority": "high",
            "title": pri,
            "description": "Technical SEO priority identified in strategy",
            "action": pri,
            "keyword": "",
            "expected_outcome": {"type": "technical_fix"},
        })

    # ── 5. Live data: 404s and critical technical issues ────────────────────────
    for issue in tech_issues:
        actions.append({
            "id": f"tech-{issue.get('type', 'unknown')}-{_url_hash(issue.get('url', ''))}",
            "type": "technical",
            "priority": "critical",
            "title": f"{issue.get('type', '').replace('_', ' ').title()}: {issue.get('url', '')}",
            "description": issue.get("message", f"Status: {issue.get('status_code', 'N/A')}"),
            "action": "Fix immediately — affects crawling and indexing",
            "target_page": issue.get("url", ""),
            "expected_outcome": {"type": "technical_fix", "target_status": 200},
        })

    # ── 6. Live data: poor Core Web Vitals ──────────────────────────────────────
    if not cwv.get("error"):
        if cwv.get("lcp", 0) > 2500:
            actions.append({
                "id": "cwv-lcp",
                "type": "technical",
                "priority": "critical",
                "title": f"Fix Slow LCP ({cwv.get('lcp', 0)}ms — target <2500ms)",
                "description": f"LCP: {cwv.get('lcp')}ms, FCP: {cwv.get('fcp')}ms, CLS: {cwv.get('cls')}",
                "action": "Preload hero image, optimise server response, use CDN",
                "expected_outcome": {"type": "lcp_improvement", "target_lcp": 2000},
            })
        elif cwv.get("performance_score", 100) < 80:
            actions.append({
                "id": "cwv-perf",
                "type": "technical",
                "priority": "high",
                "title": f"Improve Performance Score ({cwv.get('performance_score', 0)}/100)",
                "description": f"LCP: {cwv.get('lcp')}ms, CLS: {cwv.get('cls')}, FCP: {cwv.get('fcp')}ms",
                "action": "Optimise images, reduce JS bundle, implement lazy loading",
                "expected_outcome": {"type": "performance_improvement", "target_score": 90},
            })

    # Deduplicate by id, keep first occurrence
    seen = set()
    unique = []
    for a in actions:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique.append(a)

    # Sort: critical → high → medium → low
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    unique.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
    return unique


# ── Main OODA function ─────────────────────────────────────────────────────────

async def run_seo_analysis_with_ooda() -> Dict[str, Any]:
    """
    Full SEO OODA loop:
    1. OBSERVE  — GSC, rankings, CWV, technical checks
    2. ORIENT   — gaps, trends, anomalies
    3. DECIDE   — load/generate strategy → convert to agent_actions
    4. Return   — ACT happens via /execute endpoint
    """
    ooda = OODALoop(agent_name="seo")

    try:
        cycle_id = await ooda.start_cycle()
        sb = get_supabase()
        observations = {}

        # ── OBSERVE ─────────────────────────────────────────────────────────────
        logger.info("👁️  OBSERVE phase starting…")

        # Fetch known pages first (sitemap + DB) — used by technical checks and strategy
        known_pages = {}
        try:
            known_pages = await seo_agent.get_known_pages()
            observations["known_pages"] = {
                "live_count": len(known_pages.get("live", set())),
                "live_pages": known_pages.get("live_list", []),
                "missing_pages": known_pages.get("missing", []),
            }
            logger.info(f"✅ Known pages: {len(known_pages.get('live', set()))} live, "
                        f"{len(known_pages.get('missing', []))} missing")
        except Exception as e:
            logger.warning(f"Known pages fetch failed: {e}")

        try:
            observations["gsc_summary"] = await seo_agent._fetch_gsc_data()
        except Exception as e:
            observations["gsc_summary"] = {"status": "error", "message": str(e)}

        try:
            observations["ranking_changes"] = await seo_agent.track_keyword_rankings()
        except Exception as e:
            observations["ranking_changes"] = {"error": str(e)}

        try:
            observations["new_opportunities"] = await seo_agent.discover_keyword_opportunities()
        except Exception as e:
            observations["new_opportunities"] = []

        try:
            observations["core_web_vitals"] = await seo_agent._check_core_web_vitals()
        except Exception as e:
            observations["core_web_vitals"] = {"error": str(e)}

        try:
            observations["technical_issues"] = await seo_agent._check_technical_seo()
        except Exception as e:
            observations["technical_issues"] = {"error": str(e)}

        try:
            result = sb.table("seo_keywords").select("*").execute()
            observations["keywords"] = result.data or []
        except Exception:
            observations["keywords"] = []

        await ooda.observe(observations)

        keywords    = observations["keywords"]
        cwv         = observations.get("core_web_vitals", {})
        tech        = observations.get("technical_issues", {})
        ranking_chg = observations.get("ranking_changes", {})
        new_opps    = observations.get("new_opportunities", [])

        ranked_count   = sum(1 for k in keywords if k.get("current_position"))
        unranked_count = len(keywords) - ranked_count
        top10_count    = sum(1 for k in keywords if k.get("current_position") and k["current_position"] <= 10)

        logger.info(f"✅ OBSERVE: {len(keywords)} keywords ({ranked_count} ranked), "
                    f"CWV={'ok' if not cwv.get('error') else 'error'}")

        # ── ORIENT ──────────────────────────────────────────────────────────────
        logger.info("🔄 ORIENT phase…")

        analysis = {"insights_count": 0, "patterns": [], "anomalies": [], "trends": {}}
        analysis["trends"] = {
            "ranked_keywords": ranked_count,
            "top10_keywords": top10_count,
            "unranked_keywords": unranked_count,
            "gsc_clicks": observations.get("gsc_summary", {}).get("total_clicks", 0),
            "gsc_impressions": observations.get("gsc_summary", {}).get("total_impressions", 0),
        }

        if new_opps:
            analysis["patterns"].append({
                "type": "untapped_keywords",
                "count": len(new_opps),
                "top": [o["keyword"] for o in new_opps[:5]]
            })

        if ranking_chg and not ranking_chg.get("error"):
            if ranking_chg.get("improved"):
                analysis["patterns"].append({
                    "type": "ranking_improvement",
                    "keywords": [k["keyword"] for k in ranking_chg["improved"][:5]]
                })
            if ranking_chg.get("declined"):
                analysis["anomalies"].append({
                    "type": "ranking_decline",
                    "severity": "high",
                    "keywords": [k["keyword"] for k in ranking_chg["declined"][:5]]
                })

        if not cwv.get("error") and cwv.get("performance_score", 100) < 80:
            analysis["anomalies"].append({
                "type": "poor_core_web_vitals",
                "severity": "critical" if cwv["performance_score"] < 50 else "high",
                "score": cwv["performance_score"]
            })

        critical_tech = []
        if not tech.get("error"):
            critical_tech = tech.get("critical", [])
            if critical_tech:
                analysis["anomalies"].append({
                    "type": "critical_technical_issues",
                    "severity": "critical",
                    "count": len(critical_tech),
                    "urls": [i.get("url") for i in critical_tech[:5]]
                })

        if unranked_count > 3:
            analysis["patterns"].append({
                "type": "content_gap",
                "count": unranked_count,
                "message": f"{unranked_count} tracked keywords have no ranking — need content"
            })

        analysis["insights_count"] = len(analysis["patterns"]) + len(analysis["anomalies"])
        await ooda.orient(analysis)
        logger.info(f"✅ ORIENT: {len(analysis['patterns'])} patterns, {len(analysis['anomalies'])} anomalies")

        # ── DECIDE ──────────────────────────────────────────────────────────────
        logger.info("🧠 DECIDE phase — loading/generating strategy…")

        strategy_result = await _ensure_strategy(keywords, sb, known_pages=known_pages)
        strategy        = strategy_result["strategy"]
        strategy_cached = strategy_result["cached"]

        actions = _strategy_to_agent_actions(
            strategy=strategy,
            keywords=keywords,
            tech_issues=critical_tech,
            cwv=cwv
        )

        # Filter out actions that suggest creating pages/content that already exists
        live_pages = known_pages.get("live", set()) if known_pages else set()
        if live_pages:
            filtered = []
            for action in actions:
                title_lower = action.get("title", "").lower()
                keyword_lower = action.get("keyword", "").lower()
                # Skip content actions for pages that already exist
                if action.get("type") == "content":
                    # Check if any live page path matches the keyword/title
                    page_exists = False
                    for page in live_pages:
                        page_lower = page.lower()
                        # Match /vs/gainsight with "gainsight alternative" etc.
                        if keyword_lower and keyword_lower in page_lower:
                            page_exists = True
                            break
                        if page_lower != "/" and page_lower.strip("/") in title_lower:
                            page_exists = True
                            break
                    if page_exists:
                        logger.info(f"⏭️  Skipping action (page exists): {action.get('title', '')}")
                        continue
                filtered.append(action)
            logger.info(f"✅ Filtered actions: {len(actions)} → {len(filtered)} (removed {len(actions) - len(filtered)} for existing pages)")
            actions = filtered

        await ooda.decide(actions)

        from shared.actions_db import save_actions
        action_ids = await save_actions("seo", actions)

        content_actions   = [a for a in actions if a.get("type") == "content"]
        technical_actions = [a for a in actions if a.get("type") == "technical"]
        onpage_actions    = [a for a in actions if a.get("type") == "on_page"]

        logger.info(f"✅ DECIDE: {len(actions)} actions → {len(action_ids)} saved "
                    f"({len(content_actions)} content, {len(technical_actions)} technical, "
                    f"{len(onpage_actions)} on-page)")

        return {
            "success": True,
            "cycle_id": cycle_id,
            "cycle_number": ooda.cycle_number,
            "ooda_status": "decided",
            "strategy_cached": strategy_cached,
            "strategy_headline": strategy.get("headline", ""),
            "actions_saved": len(action_ids),
            "summary": {
                "total_actions":     len(actions),
                "critical":          sum(1 for a in actions if a.get("priority") == "critical"),
                "high":              sum(1 for a in actions if a.get("priority") == "high"),
                "medium":            sum(1 for a in actions if a.get("priority") == "medium"),
                "content_actions":   len(content_actions),
                "technical_actions": len(technical_actions),
                "onpage_actions":    len(onpage_actions),
                "insights_discovered": analysis["insights_count"],
            },
            "observations": {
                "gsc_live_data":     observations.get("gsc_summary"),
                "ranking_changes":   ranking_chg,
                "new_opportunities": new_opps[:5],
                "core_web_vitals":   cwv,
                "keywords_tracked":  len(keywords),
                "ranked_keywords":   ranked_count,
                "unranked_keywords": unranked_count,
            },
            "analysis": analysis,
            "actions": actions[:20],
        }

    except Exception as e:
        logger.error(f"❌ OODA loop failed: {e}")
        await ooda.fail_cycle(str(e))
        raise
