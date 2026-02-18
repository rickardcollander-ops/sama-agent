"""
Ads Agent /analyze endpoint with OODA loop implementation
"""

from typing import Dict, Any
from shared.ooda_templates import run_agent_ooda_cycle, create_analysis_structure, add_pattern, add_anomaly, create_action
from agents.ads import ads_agent


async def run_ads_analysis_with_ooda() -> Dict[str, Any]:
    """Run Ads analysis using OODA loop"""
    
    async def observe():
        """OBSERVE: Fetch campaign data from Google Ads API"""
        observations = {}
        
        try:
            observations["campaigns"] = await ads_agent.get_campaign_performance(date_range=30)
        except Exception as e:
            observations["campaigns"] = []
        
        try:
            observations["keywords"] = await ads_agent.get_keyword_performance(date_range=14)
        except Exception as e:
            observations["keywords"] = []
        
        try:
            observations["search_terms"] = await ads_agent.get_search_terms_report(date_range=7)
        except Exception as e:
            observations["search_terms"] = []
        
        observations["campaign_structure"] = ads_agent.CAMPAIGN_STRUCTURE
        
        return observations
    
    async def orient(observations):
        """ORIENT: Analyze campaign performance"""
        analysis = create_analysis_structure()
        
        campaigns = observations.get("campaigns", [])
        keywords = observations.get("keywords", [])
        search_terms = observations.get("search_terms", [])
        
        # Analyze high CPA campaigns
        high_cpa = [c for c in campaigns if c.get("cpa", 0) > 100 and c.get("conversions", 0) > 0]
        if high_cpa:
            add_anomaly(analysis, "high_cpa", "high", {"count": len(high_cpa), "campaigns": [c["name"] for c in high_cpa[:3]]})
        
        # Analyze low CTR campaigns
        low_ctr = [c for c in campaigns if c.get("ctr", 0) < 1.0 and c.get("impressions", 0) > 500]
        if low_ctr:
            add_anomaly(analysis, "low_ctr", "high", {"count": len(low_ctr), "campaigns": [c["name"] for c in low_ctr[:3]]})
        
        # Analyze campaigns with no conversions
        no_conv = [c for c in campaigns if c.get("conversions", 0) == 0 and c.get("cost", 0) > 50]
        if no_conv:
            add_anomaly(analysis, "no_conversions", "critical", {"count": len(no_conv), "spend": sum(c.get("cost", 0) for c in no_conv)})
        
        # Analyze low quality score keywords
        low_qs = [k for k in keywords if k.get("quality_score") and k.get("quality_score") < 5]
        if low_qs:
            add_anomaly(analysis, "low_quality_scores", "high", {"count": len(low_qs)})
        
        # Negative keyword opportunities
        neg_candidates = [t for t in search_terms if t.get("ctr", 0) < 0.3 and t.get("conversions", 0) == 0 and t.get("impressions", 0) >= 100]
        if neg_candidates:
            add_pattern(analysis, "negative_keyword_opportunities", {"count": len(neg_candidates)})
        
        return analysis
    
    async def decide(analysis, observations):
        """DECIDE: Generate actions based on analysis"""
        actions = []
        campaigns = observations.get("campaigns", [])
        keywords = observations.get("keywords", [])
        search_terms = observations.get("search_terms", [])
        
        # Actions for high CPA
        for camp in campaigns:
            cpa = camp.get("cpa", 0)
            if cpa > 100 and camp.get("conversions", 0) > 0:
                actions.append(create_action(
                    f"ads-cpa-{camp['name'][:20]}",
                    "bid_optimization",
                    "high",
                    f"Reduce CPA for '{camp['name']}' (${cpa:.0f})",
                    f"CPA ${cpa:.2f} exceeds $100 target. {camp.get('conversions', 0):.0f} conversions at ${camp.get('cost', 0):.2f} spend.",
                    "Lower bids, tighten targeting, or pause underperforming keywords",
                    {"type": "cpa_reduction", "target_cpa": 100},
                    campaign=camp['name']
                ))
        
        # Actions for low CTR
        for camp in campaigns:
            ctr = camp.get("ctr", 0)
            if ctr < 1.0 and camp.get("impressions", 0) > 500:
                actions.append(create_action(
                    f"ads-ctr-{camp['name'][:20]}",
                    "ad_copy",
                    "high",
                    f"Improve CTR for '{camp['name']}' ({ctr:.1f}%)",
                    f"CTR {ctr:.2f}% is below 1% threshold with {camp.get('impressions', 0)} impressions.",
                    "Generate new RSA variants with better headlines and descriptions",
                    {"type": "ctr_improvement", "target_ctr": 2.0},
                    campaign=camp['name']
                ))
        
        # Actions for no conversions
        for camp in campaigns:
            if camp.get("conversions", 0) == 0 and camp.get("cost", 0) > 50:
                actions.append(create_action(
                    f"ads-noconv-{camp['name'][:20]}",
                    "budget",
                    "critical",
                    f"No conversions: '{camp['name']}' (${camp.get('cost', 0):.0f} spent)",
                    f"${camp.get('cost', 0):.2f} spent with 0 conversions. Consider pausing or restructuring.",
                    "Pause campaign or reallocate budget to top performers",
                    {"type": "budget_reallocation", "expected_savings": camp.get('cost', 0)},
                    campaign=camp['name']
                ))
        
        # Actions for low quality score
        for kw in keywords:
            qs = kw.get("quality_score")
            if qs and qs < 5:
                actions.append(create_action(
                    f"ads-qs-{kw['keyword'][:20]}",
                    "quality_score",
                    "high",
                    f"Low Quality Score: '{kw['keyword']}' (QS={qs})",
                    f"Quality Score {qs}/10 increases CPC and reduces ad rank.",
                    "Improve ad relevance, landing page experience, and expected CTR",
                    {"type": "qs_improvement", "target_qs": 7},
                    keyword=kw['keyword'],
                    campaign=kw.get('campaign', '')
                ))
        
        # Negative keyword action
        neg_candidates = [t for t in search_terms if t.get("ctr", 0) < 0.3 and t.get("conversions", 0) == 0 and t.get("impressions", 0) >= 100]
        if neg_candidates:
            actions.append(create_action(
                "ads-negatives",
                "negative_keywords",
                "medium",
                f"Add {len(neg_candidates)} negative keywords",
                f"Found {len(neg_candidates)} search terms with <0.3% CTR and 0 conversions.",
                "Add these terms as negative keywords to stop wasted spend",
                {"type": "cost_savings", "expected_savings": sum(t.get("cost", 0) for t in neg_candidates)},
                terms=[t.get("search_term", "") for t in neg_candidates[:10]]
            ))
        
        # Check for missing campaigns
        existing_names = [c.get("name", "").lower() for c in campaigns]
        for camp_type, config in ads_agent.CAMPAIGN_STRUCTURE.items():
            if not any(config["name"].lower() in n for n in existing_names):
                actions.append(create_action(
                    f"ads-create-{camp_type}",
                    "campaign_creation",
                    "medium",
                    f"Create missing campaign: {config['name']}",
                    f"Campaign type '{camp_type}' not found in Google Ads account.",
                    f"Create {config['name']} campaign with keywords: {', '.join(config['keywords'][:3])}",
                    {"type": "new_traffic_source", "expected_impressions": 1000},
                    campaign_type=camp_type
                ))
        
        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
        
        return actions
    
    result = await run_agent_ooda_cycle("ads", observe, orient, decide)
    
    # Save actions to database
    from shared.actions_db import save_actions
    if result.get("success") and result.get("actions"):
        action_ids = await save_actions("ads", result["actions"])
        result["actions_saved"] = len(action_ids)
    
    return result
