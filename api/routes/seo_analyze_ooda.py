"""
SEO Agent /analyze endpoint with full OODA loop implementation
This will replace the existing /analyze in seo.py
"""

from typing import Dict, Any, List
from shared.ooda_loop import OODALoop
from agents.seo import seo_agent
from shared.database import get_supabase


async def run_seo_analysis_with_ooda() -> Dict[str, Any]:
    """
    Run full SEO analysis using OODA loop:
    OBSERVE → ORIENT → DECIDE → ACT (via /execute) → REFLECT
    """
    ooda = OODALoop(agent_name="seo")
    
    try:
        # Start new OODA cycle
        cycle_id = await ooda.start_cycle()
        
        # ═══════════════════════════════════════════════════════════
        # PHASE 1: OBSERVE - Fetch data from external sources
        # ═══════════════════════════════════════════════════════════
        observations = {}
        
        # Fetch GSC summary data
        try:
            observations["gsc_summary"] = await seo_agent._fetch_gsc_data()
        except Exception as e:
            observations["gsc_summary"] = {"status": "error", "message": str(e)}
        
        # Track keyword rankings from GSC
        try:
            observations["ranking_changes"] = await seo_agent.track_keyword_rankings()
        except Exception as e:
            observations["ranking_changes"] = {"error": str(e)}
        
        # Discover new keyword opportunities
        try:
            observations["new_opportunities"] = await seo_agent.discover_keyword_opportunities()
        except Exception as e:
            observations["new_opportunities"] = []
        
        # Check Core Web Vitals
        try:
            observations["core_web_vitals"] = await seo_agent._check_core_web_vitals()
        except Exception as e:
            observations["core_web_vitals"] = {"error": str(e)}
        
        # Technical SEO checks
        try:
            observations["technical_issues"] = await seo_agent._check_technical_seo()
        except Exception as e:
            observations["technical_issues"] = {"error": str(e)}
        
        # Fetch keywords from Supabase
        try:
            sb = get_supabase()
            result = sb.table("seo_keywords").select("*").execute()
            observations["keywords"] = result.data or []
        except Exception as e:
            observations["keywords"] = []
        
        # Record OBSERVE phase
        await ooda.observe(observations)
        
        # ═══════════════════════════════════════════════════════════
        # PHASE 2: ORIENT - Analyze and understand the data
        # ═══════════════════════════════════════════════════════════
        analysis = {
            "insights_count": 0,
            "patterns": [],
            "anomalies": [],
            "trends": {}
        }
        
        # Analyze ranking changes
        ranking_changes = observations.get("ranking_changes", {})
        if ranking_changes and not ranking_changes.get("error"):
            improved = ranking_changes.get("improved", [])
            declined = ranking_changes.get("declined", [])
            
            if improved:
                analysis["patterns"].append({
                    "type": "ranking_improvement",
                    "count": len(improved),
                    "keywords": [k["keyword"] for k in improved[:5]]
                })
            
            if declined:
                analysis["anomalies"].append({
                    "type": "ranking_decline",
                    "severity": "high" if len(declined) > 5 else "medium",
                    "count": len(declined),
                    "keywords": [k["keyword"] for k in declined[:5]]
                })
        
        # Analyze Core Web Vitals
        cwv = observations.get("core_web_vitals", {})
        if not cwv.get("error"):
            perf_score = cwv.get("performance_score", 100)
            if perf_score < 80:
                analysis["anomalies"].append({
                    "type": "poor_performance",
                    "severity": "critical" if perf_score < 50 else "high",
                    "score": perf_score
                })
        
        # Analyze keyword opportunities
        new_opps = observations.get("new_opportunities", [])
        if new_opps:
            analysis["insights_count"] += len(new_opps)
            analysis["patterns"].append({
                "type": "untapped_keywords",
                "count": len(new_opps),
                "top_opportunities": [o["keyword"] for o in new_opps[:5]]
            })
        
        # Technical issues analysis
        tech = observations.get("technical_issues", {})
        if not tech.get("error"):
            critical_count = len(tech.get("critical", []))
            if critical_count > 0:
                analysis["anomalies"].append({
                    "type": "critical_technical_issues",
                    "severity": "critical",
                    "count": critical_count
                })
        
        analysis["insights_count"] = len(analysis["patterns"]) + len(analysis["anomalies"])
        
        # Record ORIENT phase
        await ooda.orient(analysis)
        
        # ═══════════════════════════════════════════════════════════
        # PHASE 3: DECIDE - Determine what actions to take
        # ═══════════════════════════════════════════════════════════
        actions = []
        
        # Actions from new keyword opportunities
        for opp in new_opps[:5]:
            actions.append({
                "id": f"discover-{opp['keyword'][:20]}",
                "type": "content",
                "priority": "medium",
                "title": f"New keyword opportunity: '{opp['keyword']}'",
                "description": f"Found in GSC: {opp['impressions']} impressions, position {opp.get('position', 'N/A')}, {opp['clicks']} clicks. Not yet tracked.",
                "action": f"Add '{opp['keyword']}' to tracked keywords and create targeted content",
                "keyword": opp["keyword"],
                "expected_outcome": {"type": "ranking_improvement", "target_position": 10},
                "status": "pending"
            })
        
        # Actions from ranking declines
        if ranking_changes and not ranking_changes.get("error"):
            for declined in ranking_changes.get("declined", []):
                if declined.get("change", 0) >= 3:
                    actions.append({
                        "id": f"decline-{declined['keyword'][:20]}",
                        "type": "on_page",
                        "priority": "high",
                        "title": f"Ranking dropped: '{declined['keyword']}' #{declined['from']} → #{declined['to']}",
                        "description": f"Lost {declined['change']} positions. Investigate and optimize.",
                        "action": f"Review and strengthen content for '{declined['keyword']}' — add internal links, update content, improve meta tags",
                        "keyword": declined["keyword"],
                        "expected_outcome": {"type": "ranking_recovery", "target_position": declined['from']},
                        "status": "pending"
                    })
            
            for lost in ranking_changes.get("lost_top_10", []):
                actions.append({
                    "id": f"lost-top10-{lost[:20]}",
                    "type": "on_page",
                    "priority": "critical",
                    "title": f"Lost top 10: '{lost}'",
                    "description": "This keyword dropped out of page 1. Immediate action needed.",
                    "action": f"Urgent: optimize page for '{lost}', build internal links, consider content refresh",
                    "keyword": lost,
                    "expected_outcome": {"type": "ranking_recovery", "target_position": 10},
                    "status": "pending"
                })
        
        # Actions from Core Web Vitals
        if not cwv.get("error"):
            if cwv.get("performance_score", 100) < 80:
                actions.append({
                    "id": "cwv-perf",
                    "type": "technical",
                    "priority": "high",
                    "title": f"Improve Performance Score ({cwv.get('performance_score', 0)}/100)",
                    "description": f"LCP: {cwv.get('lcp', 0)}ms, CLS: {cwv.get('cls', 0)}, FCP: {cwv.get('fcp', 0)}ms",
                    "action": "Optimize images, reduce JS bundle, implement lazy loading",
                    "expected_outcome": {"type": "performance_improvement", "target_score": 90},
                    "status": "pending"
                })
            
            if cwv.get("lcp", 0) > 2500:
                actions.append({
                    "id": "cwv-lcp",
                    "type": "technical",
                    "priority": "critical",
                    "title": f"Fix Slow LCP ({cwv.get('lcp', 0)}ms)",
                    "description": "Largest Contentful Paint exceeds 2500ms threshold",
                    "action": "Preload hero image, optimize server response time, use CDN",
                    "expected_outcome": {"type": "lcp_improvement", "target_lcp": 2000},
                    "status": "pending"
                })
        
        # Actions from technical issues
        if not tech.get("error"):
            for issue in tech.get("critical", []):
                actions.append({
                    "id": f"tech-{issue.get('type', 'unknown')}-{issue.get('url', '')[:20]}",
                    "type": "technical",
                    "priority": "critical",
                    "title": f"{issue.get('type', '').replace('_', ' ').title()}: {issue.get('url', '')}",
                    "description": issue.get("message", f"Status: {issue.get('status_code', 'N/A')}"),
                    "action": "Fix immediately - affects crawling and indexing",
                    "expected_outcome": {"type": "technical_fix", "target_status": 200},
                    "status": "pending"
                })
        
        # Actions from keyword gaps
        for kw in observations.get("keywords", []):
            pos = kw.get("current_position", 0)
            keyword = kw.get("keyword", "")
            impressions = kw.get("current_impressions", 0)
            clicks = kw.get("current_clicks", 0)
            
            if impressions > 50 and pos > 10:
                actions.append({
                    "id": f"content-{keyword[:20]}",
                    "type": "content",
                    "priority": "high",
                    "title": f"Create/optimize content for '{keyword}'",
                    "description": f"Position {pos}, {impressions} impressions but only {clicks} clicks",
                    "action": f"Generate SEO-optimized blog post targeting '{keyword}'",
                    "keyword": keyword,
                    "expected_outcome": {"type": "ranking_improvement", "target_position": 10, "target_clicks": impressions * 0.05},
                    "status": "pending"
                })
        
        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
        
        # Record DECIDE phase
        await ooda.decide(actions)
        
        # Return analysis results (ACT phase happens via /execute endpoint)
        return {
            "success": True,
            "cycle_id": cycle_id,
            "cycle_number": ooda.cycle_number,
            "ooda_status": "decided",
            "summary": {
                "total_actions": len(actions),
                "critical": sum(1 for a in actions if a["priority"] == "critical"),
                "high": sum(1 for a in actions if a["priority"] == "high"),
                "medium": sum(1 for a in actions if a["priority"] == "medium"),
                "insights_discovered": analysis["insights_count"],
                "patterns_found": len(analysis["patterns"]),
                "anomalies_detected": len(analysis["anomalies"])
            },
            "observations": {
                "gsc_live_data": observations.get("gsc_summary"),
                "ranking_changes": observations.get("ranking_changes"),
                "new_opportunities": observations.get("new_opportunities", [])[:5],
                "core_web_vitals": observations.get("core_web_vitals"),
                "keywords_tracked": len(observations.get("keywords", []))
            },
            "analysis": analysis,
            "actions": actions
        }
    
    except Exception as e:
        await ooda.fail_cycle(str(e))
        raise
