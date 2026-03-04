"""
Content Agent /analyze endpoint with OODA loop implementation
"""

from typing import Dict, Any
from shared.ooda_templates import run_agent_ooda_cycle, create_analysis_structure, add_pattern, add_anomaly, create_action
from shared.database import get_supabase
from agents.brand_voice import brand_voice


async def run_content_analysis_with_ooda() -> Dict[str, Any]:
    """Run Content analysis using OODA loop"""
    
    async def observe():
        """OBSERVE: Fetch content and keyword data, run competitor gap analysis"""
        observations = {}
        sb = get_supabase()

        # Fetch existing content
        try:
            result = sb.table("content_pieces").select("*").order("created_at", desc=True).limit(100).execute()
            observations["content_pieces"] = result.data or []
        except Exception:
            observations["content_pieces"] = []

        # Update keyword data from GSC
        try:
            from agents.seo import seo_agent
            await seo_agent.track_keyword_rankings()
        except Exception:
            pass

        # Fetch SEO keywords
        try:
            kw_result = sb.table("seo_keywords").select("*").execute()
            observations["keywords"] = kw_result.data or []
        except Exception:
            observations["keywords"] = []

        observations["content_pillars"] = brand_voice.CONTENT_PILLARS
        observations["competitors"] = ["gainsight", "totango", "churnzero"]

        # Run competitor content gap analysis
        try:
            from agents.content import content_agent
            gap_result = await content_agent.analyze_competitor_content_gaps()
            observations["competitor_gaps"] = gap_result
        except Exception:
            observations["competitor_gaps"] = {"gaps": [], "coverage": {}, "total_gaps": 0}

        return observations
    
    async def orient(observations):
        """ORIENT: Analyze content gaps, competitor themes, and opportunities"""
        analysis = create_analysis_structure()

        content_pieces = observations.get("content_pieces", [])
        keywords = observations.get("keywords", [])
        competitor_gaps = observations.get("competitor_gaps", {})

        # Analyze keyword-level content gaps
        existing_keywords = {cp.get("target_keyword", "").lower() for cp in content_pieces if cp.get("target_keyword")}
        gap_keywords = [kw for kw in keywords if kw.get("keyword", "").lower() not in existing_keywords]

        if gap_keywords:
            add_pattern(analysis, "content_gaps", {
                "count": len(gap_keywords),
                "high_value": len([k for k in gap_keywords if k.get("current_impressions", 0) > 100])
            })

        # Competitor theme coverage gaps
        coverage = competitor_gaps.get("coverage", {})
        for comp_key, comp_cov in coverage.items():
            gap_count = comp_cov.get("gaps", 0)
            if gap_count > 0:
                add_pattern(analysis, f"competitor_gap_{comp_key}", {
                    "competitor": comp_cov.get("name", comp_key),
                    "themes_missing": gap_count,
                    "coverage_pct": comp_cov.get("coverage_pct", 0),
                })

        total_comp_gaps = competitor_gaps.get("total_gaps", 0)
        if total_comp_gaps > 5:
            add_anomaly(analysis, "significant_competitor_content_deficit", "high", {
                "total_gaps": total_comp_gaps,
                "description": "Competitors cover many themes we don't -- significant content opportunity.",
            })

        # Analyze thin content
        thin_content = [cp for cp in content_pieces if cp.get("word_count", 0) > 0 and cp.get("word_count", 0) < 1000 and cp.get("content_type") == "blog"]
        if thin_content:
            add_anomaly(analysis, "thin_content", "high", {"count": len(thin_content)})

        # Analyze missing meta descriptions
        missing_meta = [cp for cp in content_pieces if not cp.get("meta_description")]
        if missing_meta:
            add_anomaly(analysis, "missing_meta", "medium", {"count": len(missing_meta)})

        # Analyze draft content
        drafts = [cp for cp in content_pieces if cp.get("status") == "draft"]
        if drafts:
            add_pattern(analysis, "unpublished_content", {"count": len(drafts)})

        return analysis
    
    async def decide(analysis, observations):
        """DECIDE: Generate content actions"""
        actions = []
        content_pieces = observations.get("content_pieces", [])
        keywords = observations.get("keywords", [])
        competitors = observations.get("competitors", [])
        
        # Actions for content gaps
        existing_keywords = {cp.get("target_keyword", "").lower() for cp in content_pieces if cp.get("target_keyword")}
        for kw in keywords:
            keyword = kw.get("keyword", "")
            if keyword.lower() not in existing_keywords:
                impressions = kw.get("current_impressions", 0)
                position = kw.get("current_position", 0)
                priority = "high" if impressions > 100 else "medium"
                
                actions.append(create_action(
                    f"content-gap-{keyword[:30]}",
                    "blog_post",
                    priority,
                    f"Create content for: '{keyword}'",
                    f"No content targeting this keyword. Position: {position}, Impressions: {impressions}.",
                    f"Generate a blog post targeting '{keyword}' to capture organic traffic",
                    {"type": "traffic_increase", "target_impressions": impressions * 1.5},
                    keyword=keyword
                ))
        
        # Actions for thin content
        for cp in content_pieces:
            word_count = cp.get("word_count", 0)
            if word_count > 0 and word_count < 1000 and cp.get("content_type") == "blog":
                actions.append(create_action(
                    f"content-thin-{cp.get('id', '')[:20]}",
                    "optimize",
                    "high",
                    f"Expand thin content: '{cp.get('title', '')[:50]}'",
                    f"Only {word_count} words. Blog posts should be 1500+ words for SEO.",
                    "Expand content with more detail, examples, and data points",
                    {"type": "ranking_improvement", "target_word_count": 1500},
                    content_id=cp.get("id", ""),
                    keyword=cp.get("target_keyword", "")
                ))
        
        # Actions for missing meta descriptions
        for cp in content_pieces:
            if not cp.get("meta_description"):
                actions.append(create_action(
                    f"content-meta-{cp.get('id', '')[:20]}",
                    "meta",
                    "medium",
                    f"Add meta description: '{cp.get('title', '')[:50]}'",
                    "Missing meta description hurts CTR in search results.",
                    "Generate an SEO-optimized meta description (150-160 chars)",
                    {"type": "ctr_improvement", "target_ctr_increase": 0.5},
                    content_id=cp.get("id", ""),
                    keyword=cp.get("target_keyword", "")
                ))
        
        # Actions for draft content
        for cp in content_pieces:
            if cp.get("status") == "draft":
                actions.append(create_action(
                    f"content-publish-{cp.get('id', '')[:20]}",
                    "publish",
                    "medium",
                    f"Publish draft: '{cp.get('title', '')[:50]}'",
                    f"Content is still in draft status. {cp.get('word_count', 0)} words, type: {cp.get('content_type', '')}.",
                    "Review and publish this content",
                    {"type": "content_published", "expected_traffic": 100},
                    content_id=cp.get("id", "")
                ))
        
        # Actions for competitor comparisons
        existing_comparisons = [cp for cp in content_pieces if cp.get("content_type") == "comparison"]
        existing_comp_names = [cp.get("title", "").lower() for cp in existing_comparisons]
        for comp in competitors:
            if not any(comp in name for name in existing_comp_names):
                actions.append(create_action(
                    f"content-comparison-{comp}",
                    "comparison",
                    "high",
                    f"Create comparison: Successifier vs {comp.title()}",
                    f"No comparison page for {comp.title()}. These pages convert well.",
                    f"Generate comparison page targeting '{comp} alternative'",
                    {"type": "conversion_opportunity", "expected_conversions": 5},
                    competitor=comp
                ))

        # Actions from competitor content gap analysis
        # These are de-duplicated against the keyword-gap actions above to
        # avoid recommending the same content twice.
        existing_action_keywords = {a.get("keyword", "").lower() for a in actions if a.get("keyword")}
        competitor_gaps = observations.get("competitor_gaps", {})
        for gap in competitor_gaps.get("gaps", []):
            target_kw = gap.get("target_keyword", "")
            if target_kw.lower() in existing_action_keywords:
                continue  # already covered by a keyword-gap action
            if target_kw.lower() in existing_keywords:
                continue  # we already have content for this keyword

            comp_name = gap.get("competitor", "")
            theme = gap.get("theme", "")
            impressions = gap.get("keyword_impressions", 0)
            rec_type = gap.get("recommended_type", "blog_post")

            action_id = f"comp-gap-{comp_name[:10]}-{theme[:20]}".lower().replace(" ", "-")
            priority = gap.get("priority", "medium")

            actions.append(create_action(
                action_id,
                rec_type,
                priority,
                gap.get("title", f"Cover competitor theme: {theme}"),
                gap.get("description", f"{comp_name} covers '{theme}' but we don't."),
                gap.get("action", f"Generate a {rec_type} about '{theme}' targeting '{target_kw}'"),
                {
                    "type": "competitor_gap_closure",
                    "competitor": comp_name,
                    "target_impressions": impressions * 1.5 if impressions else 50,
                },
                keyword=target_kw,
                competitor=comp_name.lower() if comp_name != "organic_opportunity" else None,
            ))
            existing_action_keywords.add(target_kw.lower())

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
        
        return actions
    
    result = await run_agent_ooda_cycle("content", observe, orient, decide)
    
    # Save actions to database
    from shared.actions_db import save_actions
    if result.get("success") and result.get("actions"):
        action_ids = await save_actions("content", result["actions"])
        result["actions_saved"] = len(action_ids)
    
    return result
