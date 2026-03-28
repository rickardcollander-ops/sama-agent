"""
SEO Agent Chat Endpoint
Natural language interface for the SEO agent.

Supported actions (examples):
  "analyze customer success platform"        → SERP analysis
  "track keyword cs automation"              → add/update keyword
  "show my keywords" / "how are we ranking"  → keyword overview
  "run audit"                                → technical SEO audit
  "fix the 404 on /vs/churnzero"             → execute technical action
  "what are our core web vitals?"            → CWV data
  "find new keyword opportunities"           → GSC discovery
  "what should I focus on?"                  → AI strategy advice
"""

import asyncio
from fastapi import APIRouter, Body, HTTPException
from typing import Dict, Any
from agents.seo import seo_agent
from shared.chat_db import save_message, get_chat_history
from shared.database import get_supabase
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/chat/history")
async def get_seo_chat_history(user_id: str = "default_user"):
    """Get chat history for SEO agent"""
    history = await get_chat_history("seo", user_id)
    return {"history": history}


@router.post("/chat")
async def chat_with_seo_agent(request: Dict[str, Any] = Body(...)):
    """
    Chat with SEO agent using natural language.

    The agent will:
    - Interpret intent via Claude
    - Pull live data from GSC / PageSpeed / Supabase
    - Execute actions (SERP analysis, keyword tracking, audit triggers, etc.)
    - Return a human-readable response
    """
    message = request.get("message", "")
    user_id = request.get("user_id", "default_user")

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    await save_message("seo", "user", message, user_id)

    if not seo_agent.client:
        response_text = "SEO agent is not configured. Please set ANTHROPIC_API_KEY in Railway."
        await save_message("seo", "agent", response_text, user_id)
        return {"response": response_text}

    try:
        sb = get_supabase()

        # ── Load chat history for context ────────────────────────────────
        history = await get_chat_history("seo", user_id)
        # Build conversation context (last 10 messages for Claude)
        conversation_context = ""
        if history and len(history) > 1:  # More than just current message
            recent = history[-11:-1]  # Last 10 messages before current
            conversation_context = "\n\nRecent conversation:\n"
            for msg in recent:
                role = "User" if msg["role"] == "user" else "Agent"
                conversation_context += f"{role}: {msg['message'][:150]}\n"

        # ── Build context ────────────────────────────────────────────────
        # Recent keywords
        try:
            kw_result = sb.table("seo_keywords").select("*").order("current_position").limit(20).execute()
            keywords = kw_result.data or []
        except Exception:
            keywords = []

        # Recent actions
        try:
            action_result = sb.table("agent_actions").select("*")\
                .eq("agent_name", "seo").eq("status", "pending")\
                .order("created_at", desc=True).limit(10).execute()
            pending_actions = action_result.data or []
        except Exception:
            pending_actions = []

        kw_summary = f"\n\nTracked keywords ({len(keywords)}):\n"
        for kw in keywords[:10]:
            pos = kw.get("current_position")
            pos_str = f"#{pos}" if pos else "unranked"
            kw_summary += f"  - '{kw.get('keyword')}' {pos_str} | {kw.get('current_clicks', 0)} clicks | {kw.get('current_impressions', 0)} impressions\n"

        action_summary = f"\nPending actions ({len(pending_actions)}):\n"
        for a in pending_actions[:5]:
            action_summary += f"  - [{a.get('priority', '?').upper()}] {a.get('title', '')}\n"

        # ── Intent classification ─────────────────────────────────────────
        _cls_prompt = f"""You are the SEO Agent for Successifier (successifier.com — AI customer success platform).
{conversation_context}
Current message: "{message}"
{kw_summary}{action_summary}

Classify their intent as ONE of:
1. SERP_ANALYSIS   – wants to analyze Google results for a keyword
2. TRACK_KEYWORD   – wants to add/track a new keyword
3. SHOW_RANKINGS   – wants to see current keyword rankings / GSC data
4. RUN_AUDIT       – wants a technical SEO audit
5. SHOW_VITALS     – wants Core Web Vitals data
6. FIND_OPPORTUNITIES – wants new keyword suggestions from GSC
7. EXECUTE_ACTION  – wants to act on a specific pending action (fix 404, create page, etc.)
8. STRATEGY        – wants strategic SEO advice
9. GENERAL         – general question or anything else

Extract:
- keyword: main keyword (if relevant)
- action_title: title snippet of a pending action to execute (if EXECUTE_ACTION)

Reply in EXACTLY this format (no extra text):
ACTION: [type]
KEYWORD: [keyword or N/A]
ACTION_TITLE: [title fragment or N/A]
EXPLANATION: [one sentence]"""

        classification = await asyncio.to_thread(
            seo_agent.client.messages.create,
            model=seo_agent.model,
            max_tokens=512,
            messages=[{"role": "user", "content": _cls_prompt}]
        )

        lines = classification.content[0].text.strip().split("\n")
        intent, keyword, action_title, explanation = "GENERAL", "", "", ""
        for line in lines:
            if line.startswith("ACTION:"):
                intent = line.split(":", 1)[1].strip()
            elif line.startswith("KEYWORD:"):
                v = line.split(":", 1)[1].strip()
                keyword = "" if v.lower() == "n/a" else v
            elif line.startswith("ACTION_TITLE:"):
                v = line.split(":", 1)[1].strip()
                action_title = "" if v.lower() == "n/a" else v
            elif line.startswith("EXPLANATION:"):
                explanation = line.split(":", 1)[1].strip()

        # ── Route to action ──────────────────────────────────────────────

        if intent == "SERP_ANALYSIS":
            if not keyword:
                response_text = "Which keyword would you like me to analyze? E.g. 'analyze customer success platform'"
            else:
                response_text = f"🔍 Running SERP analysis for **'{keyword}'**...\n"
                from agents.seo_serp import serp_analyzer
                result = await serp_analyzer.analyze_serp(keyword, num_results=5)
                if result.get("success"):
                    insights = result.get("insights", {})
                    comp = insights.get("competitive_analysis", {})
                    recs = insights.get("recommendations", [])
                    topics = insights.get("common_topics", [])[:5]
                    response_text += (
                        f"\n📊 **Top {result.get('results_analyzed')} results analyzed:**\n"
                        f"  • Avg word count: **{comp.get('avg_word_count', 0):,}** words "
                        f"(range {comp.get('min_word_count', 0):,}–{comp.get('max_word_count', 0):,})\n"
                        f"  • Schema adoption: {insights.get('schema_usage_percentage', 0):.0f}% of top pages\n"
                        f"  • Avg H2 headings: {insights.get('recommended_h2_count', 0)}\n"
                    )
                    if topics:
                        response_text += f"\n🏷️ **Common topics in top results:**\n"
                        for t in topics:
                            response_text += f"  - {t}\n"
                    if recs:
                        response_text += f"\n💡 **Recommendations for successifier.com:**\n"
                        for r in recs:
                            response_text += f"  ✅ {r}\n"
                else:
                    response_text += f"\n⚠️ {result.get('error', 'Analysis failed')}"

        elif intent == "TRACK_KEYWORD":
            if not keyword:
                response_text = "Which keyword do you want to track? E.g. 'track keyword churn reduction'"
            else:
                existing = [k for k in keywords if k.get("keyword", "").lower() == keyword.lower()]
                if existing:
                    kw = existing[0]
                    pos = kw.get("current_position")
                    response_text = (
                        f"📌 **'{keyword}'** is already tracked.\n"
                        f"  • Position: {'#' + str(pos) if pos else 'not yet ranked'}\n"
                        f"  • Clicks (28d): {kw.get('current_clicks', 0)}\n"
                        f"  • Impressions (28d): {kw.get('current_impressions', 0)}"
                    )
                else:
                    try:
                        sb.table("seo_keywords").insert({
                            "keyword": keyword.lower(),
                            "priority": "medium",
                            "intent": "informational",
                            "target_page": "/blog"
                        }).execute()
                        response_text = (
                            f"✅ Added **'{keyword}'** to tracked keywords.\n\n"
                            f"GSC data will populate on the next analysis run. "
                            f"Click **Analyze** to fetch rankings now."
                        )
                    except Exception as e:
                        response_text = f"⚠️ Could not save keyword: {e}"

        elif intent == "SHOW_RANKINGS":
            if not keywords:
                response_text = "No keywords tracked yet. Click **Analyze** to pull data from Google Search Console."
            else:
                ranked = [k for k in keywords if k.get("current_position")]
                top3 = [k for k in ranked if k["current_position"] <= 3]
                top10 = [k for k in ranked if k["current_position"] <= 10]
                total_clicks = sum(k.get("current_clicks", 0) for k in keywords)
                total_impressions = sum(k.get("current_impressions", 0) for k in keywords)

                response_text = (
                    f"📈 **SEO Rankings Overview** (last 28 days)\n\n"
                    f"  • Keywords tracked: {len(keywords)}\n"
                    f"  • Top 3: {len(top3)} keywords\n"
                    f"  • Top 10: {len(top10)} keywords\n"
                    f"  • Total clicks: {total_clicks:,}\n"
                    f"  • Total impressions: {total_impressions:,}\n"
                )
                if top3:
                    response_text += "\n🥇 **Top 3 keywords:**\n"
                    for k in top3[:5]:
                        response_text += f"  #{k['current_position']} '{k['keyword']}' — {k.get('current_clicks', 0)} clicks\n"
                declining = [k for k in keywords if k.get("current_position", 0) > 10 and k.get("current_impressions", 0) > 50]
                if declining:
                    response_text += "\n⚠️ **Needs attention (>pos 10 but high impressions):**\n"
                    for k in declining[:3]:
                        response_text += f"  #{k['current_position']} '{k['keyword']}' — {k.get('current_impressions', 0)} impressions\n"

        elif intent == "RUN_AUDIT":
            response_text = "🔧 Triggering SEO audit...\n"
            try:
                audit = await seo_agent.run_weekly_audit()
                critical = len(audit.get("critical_issues", []))
                high = len(audit.get("high_issues", []))
                medium = len(audit.get("medium_issues", []))
                cwv = audit.get("core_web_vitals", {})
                response_text += (
                    f"\n✅ **Audit complete:**\n"
                    f"  • Critical issues: {critical}\n"
                    f"  • High issues: {high}\n"
                    f"  • Medium issues: {medium}\n"
                )
                if cwv and not cwv.get("error"):
                    response_text += (
                        f"\n⚡ **Core Web Vitals:**\n"
                        f"  • Performance score: {cwv.get('performance_score', 'N/A')}/100\n"
                        f"  • LCP: {cwv.get('lcp', 'N/A')}ms\n"
                        f"  • CLS: {cwv.get('cls', 'N/A')}\n"
                    )
                recs = audit.get("recommendations", [])
                if recs:
                    response_text += "\n💡 **Top recommendations:**\n"
                    for r in recs[:3]:
                        if r.strip():
                            response_text += f"  ✅ {r}\n"
                response_text += "\nFull results saved to Audit History tab."
            except Exception as e:
                response_text += f"\n⚠️ Audit error: {e}"

        elif intent == "SHOW_VITALS":
            try:
                cwv = await seo_agent._check_core_web_vitals()
                if cwv.get("error"):
                    response_text = f"⚠️ Could not fetch vitals: {cwv['error']}"
                else:
                    score = cwv.get("performance_score", 0)
                    score_emoji = "🟢" if score >= 90 else "🟡" if score >= 50 else "🔴"
                    lcp = cwv.get("lcp", 0)
                    lcp_emoji = "🟢" if lcp <= 2500 else "🔴"
                    cls = cwv.get("cls", 0)
                    cls_emoji = "🟢" if cls <= 0.1 else "🔴"
                    ttfb = cwv.get("ttfb", 0)
                    ttfb_emoji = "🟢" if ttfb <= 800 else "🟡" if ttfb <= 1800 else "🔴"
                    response_text = (
                        f"⚡ **Core Web Vitals — successifier.com**\n\n"
                        f"  **Mobile** {score_emoji} {score}/100\n"
                        f"  {lcp_emoji} LCP: **{lcp:,.0f}ms** (target <2,500ms)\n"
                        f"  {cls_emoji} CLS: **{cls}** (target <0.1)\n"
                        f"  {ttfb_emoji} TTFB: **{ttfb:,.0f}ms** (server speed, target <800ms)\n"
                        f"  • FCP: {cwv.get('fcp', 0):,.0f}ms · TBT: {cwv.get('tbt', 0):,.0f}ms\n"
                    )
                    desktop = cwv.get("desktop", {})
                    if desktop and not desktop.get("error"):
                        d_score = desktop.get("performance_score", 0)
                        d_emoji = "🟢" if d_score >= 90 else "🟡" if d_score >= 50 else "🔴"
                        response_text += f"\n  **Desktop** {d_emoji} {d_score}/100  · LCP: {desktop.get('lcp', 0):,.0f}ms\n"
                    if score < 90:
                        response_text += "\n💡 Say **'run audit'** for a full analysis and fix recommendations."
            except Exception as e:
                response_text = f"⚠️ Could not fetch Core Web Vitals: {e}"

        elif intent == "FIND_OPPORTUNITIES":
            response_text = "🔎 Discovering keyword opportunities from GSC...\n"
            try:
                opportunities = await seo_agent.discover_keyword_opportunities()
                if opportunities:
                    quick_wins = [o for o in opportunities if o.get("category") == "quick_win"]
                    untapped   = [o for o in opportunities if o.get("category") != "quick_win"]
                    response_text += f"\n✅ Found **{len(opportunities)} opportunities:**\n"
                    if quick_wins:
                        response_text += f"\n⚡ **Page-2 quick wins** (easiest to rank):\n"
                        for opp in quick_wins[:4]:
                            pos = opp.get('position') or '?'
                            response_text += (
                                f"  • **'{opp['keyword']}'** — pos {pos:.0f}, "
                                f"{opp['impressions']} impr, score {opp.get('opportunity_score',0):.0f}\n"
                            )
                    if untapped:
                        response_text += f"\n🎯 **Untapped** (high impressions, no clicks):\n"
                        for opp in untapped[:4]:
                            response_text += (
                                f"  • **'{opp['keyword']}'** — "
                                f"{opp['impressions']} impr, {opp['clicks']} clicks\n"
                            )
                    response_text += "\nSay **'track keyword [name]'** to start tracking any of these."
                else:
                    response_text += "\nNo new opportunities found — all high-impression queries are already tracked."
            except Exception as e:
                response_text += f"\n⚠️ Error: {e}"

        elif intent == "EXECUTE_ACTION":
            if not action_title or not pending_actions:
                response_text = (
                    f"I found {len(pending_actions)} pending actions. "
                    "Be more specific — e.g. 'fix the 404 on /vs/churnzero' or 'execute the LCP action'."
                )
            else:
                match = next(
                    (a for a in pending_actions if action_title.lower() in a.get("title", "").lower()),
                    None
                )
                if not match:
                    response_text = (
                        f"❌ Couldn't find a pending action matching **'{action_title}'**.\n"
                        f"Pending: {', '.join(a.get('title','') for a in pending_actions[:5])}"
                    )
                else:
                    response_text = f"⚙️ Executing: **{match.get('title')}**...\n"
                    from api.routes.seo import execute_action
                    result = await execute_action(match)
                    if result.get("success"):
                        detail = (
                            result.get("suggestions") or
                            result.get("fix_plan") or
                            result.get("message") or
                            f"Action type: {result.get('action_type', 'done')}"
                        )
                        response_text += f"\n✅ Done!\n\n{detail}"
                    else:
                        response_text += f"\n⚠️ Failed: {result.get('error', result.get('message', 'Unknown error'))}"

        elif intent == "STRATEGY":
            gsc_summary = ""
            try:
                gsc = await seo_agent._fetch_gsc_data()
                if gsc.get("status") == "ok":
                    gsc_summary = (
                        f"\nGSC last 28 days: {gsc.get('total_clicks')} clicks, "
                        f"{gsc.get('total_impressions')} impressions, "
                        f"avg pos {gsc.get('avg_position')}"
                    )
            except Exception as e:
                logger.debug(f"Failed to fetch GSC data for strategy context: {e}")

            _strategy_prompt = f"""You are the SEO Agent for Successifier (successifier.com — AI customer success platform targeting SMB SaaS companies).
{conversation_context}
Current question: "{message}"

Context:
- Competitors: Gainsight, Totango, ChurnZero
- Tracked keywords: {len(keywords)}, {len([k for k in keywords if k.get('current_position', 99) <= 10])} in top 10
- Pending actions: {len(pending_actions)}
{gsc_summary}

Give specific, actionable SEO strategy advice. Reference real successifier.com pages and real competitors where relevant. Be concise (max 5 bullet points)."""
            strategy_answer = await asyncio.to_thread(
                seo_agent.client.messages.create,
                model=seo_agent.model,
                max_tokens=800,
                messages=[{"role": "user", "content": _strategy_prompt}]
            )
            response_text = strategy_answer.content[0].text

        else:
            # GENERAL fallback
            _general_prompt = f"""You are the SEO Agent for Successifier (successifier.com — AI customer success platform).
{conversation_context}
Current message: "{message}"

Answer helpfully and concisely. If they want to take action, tell them exactly what to say (e.g. 'Say "run audit" to trigger a technical audit').

What you can do:
- Analyze SERP for any keyword
- Track new keywords
- Show rankings & GSC data
- Run technical audits
- Check Core Web Vitals
- Find keyword opportunities (with opportunity scoring by missed clicks)
- Execute pending actions
- Give SEO strategy advice"""
            general_answer = await asyncio.to_thread(
                seo_agent.client.messages.create,
                model=seo_agent.model,
                max_tokens=512,
                messages=[{"role": "user", "content": _general_prompt}]
            )
            response_text = general_answer.content[0].text

        await save_message("seo", "agent", response_text, user_id)
        return {"response": response_text}

    except Exception as e:
        logger.error(f"SEO chat error: {e}", exc_info=True)
        error_response = f"Sorry, something went wrong: {str(e)}\n\nTry rephrasing, or check the backend logs."
        await save_message("seo", "agent", error_response, user_id)
        return {"response": error_response}
