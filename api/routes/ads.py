from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from agents.ads import ads_agent

router = APIRouter()


class RSARequest(BaseModel):
    campaign: str
    ad_group: str
    target_keyword: Optional[str] = None


class BidOptimizationRequest(BaseModel):
    campaign_id: str
    performance_data: Dict[str, Any]


class NegativeKeywordRequest(BaseModel):
    search_terms_report: List[Dict[str, Any]]


class CampaignCreateRequest(BaseModel):
    campaign_type: str  # brand, core_product, churn_prevention, etc.


@router.get("/status")
async def get_status():
    """Get Google Ads agent status"""
    return {
        "agent": "ads",
        "status": "operational",
        "campaigns": list(ads_agent.CAMPAIGN_STRUCTURE.keys()),
        "optimization_rules": len(ads_agent.OPTIMIZATION_RULES),
        "rsa_headline_bank": len(ads_agent.RSA_HEADLINE_BANK)
    }


@router.get("/campaigns")
async def get_campaigns():
    """Get all campaign configurations"""
    return {
        "campaigns": ads_agent.CAMPAIGN_STRUCTURE
    }


@router.post("/campaigns/create")
async def create_campaign(request: CampaignCreateRequest):
    """Create a new Google Ads campaign"""
    try:
        result = await ads_agent.create_campaign(request.campaign_type)
        return {"success": True, "campaign": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rsa/generate")
async def generate_rsa(request: RSARequest):
    """Generate Responsive Search Ad variants"""
    try:
        result = await ads_agent.generate_rsa(
            campaign=request.campaign,
            ad_group=request.ad_group,
            target_keyword=request.target_keyword
        )
        return {"success": True, "rsa": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize")
async def optimize_campaigns():
    """Quick optimize all campaigns"""
    try:
        results = await ads_agent.run_daily_optimization()
        return {"success": True, "message": "Campaign optimization started", "results": results}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/optimize/bids")
async def optimize_bids(request: BidOptimizationRequest):
    """Optimize bids based on performance data"""
    try:
        result = await ads_agent.optimize_bids(
            campaign_id=request.campaign_id,
            performance_data=request.performance_data
        )
        return {"success": True, "optimizations": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/negative-keywords/harvest")
async def harvest_negative_keywords(request: NegativeKeywordRequest):
    """Harvest negative keywords from search terms report"""
    try:
        negative_keywords = await ads_agent.harvest_negative_keywords(
            search_terms_report=request.search_terms_report
        )
        return {
            "success": True,
            "negative_keywords": negative_keywords,
            "count": len(negative_keywords)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}/analyze")
async def analyze_campaign(campaign_id: str, date_range: int = 30):
    """Analyze campaign performance"""
    try:
        analysis = await ads_agent.analyze_campaign_performance(
            campaign_id=campaign_id,
            date_range=date_range
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize/daily")
async def run_daily_optimization(background_tasks: BackgroundTasks):
    """Run daily optimization routine"""
    try:
        background_tasks.add_task(ads_agent.run_daily_optimization)
        return {
            "success": True,
            "message": "Daily optimization started in background"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize/daily/sync")
async def run_daily_optimization_sync():
    """Run daily optimization synchronously (for testing)"""
    try:
        results = await ads_agent.run_daily_optimization()
        return {"success": True, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rsa/headline-bank")
async def get_headline_bank():
    """Get RSA headline bank"""
    return {
        "headlines": ads_agent.RSA_HEADLINE_BANK,
        "descriptions": ads_agent.RSA_DESCRIPTION_BANK
    }


@router.get("/optimization-rules")
async def get_optimization_rules():
    """Get all optimization rules"""
    return {
        "rules": ads_agent.OPTIMIZATION_RULES
    }


@router.post("/analyze")
async def run_full_analysis():
    """Run full Ads analysis and return actionable items"""
    actions = []
    campaign_data = []
    keyword_data = []
    search_terms = []
    
    # 1. Fetch campaign performance
    try:
        campaign_data = await ads_agent.get_campaign_performance(date_range=30)
        for camp in campaign_data:
            ctr = camp.get("ctr", 0)
            cpa = camp.get("cpa", 0)
            conversions = camp.get("conversions", 0)
            cost = camp.get("cost", 0)
            impressions = camp.get("impressions", 0)
            name = camp.get("name", "Unknown")
            
            # High CPA campaigns
            if cpa > 100 and conversions > 0:
                actions.append({
                    "id": f"ads-cpa-{name[:20]}",
                    "type": "bid_optimization",
                    "priority": "high",
                    "title": f"Reduce CPA for '{name}' (${cpa:.0f})",
                    "description": f"CPA ${cpa:.2f} exceeds $100 target. {conversions:.0f} conversions at ${cost:.2f} spend.",
                    "action": "Lower bids, tighten targeting, or pause underperforming keywords",
                    "campaign": name,
                    "status": "pending"
                })
            
            # Low CTR campaigns
            if ctr < 1.0 and impressions > 500:
                actions.append({
                    "id": f"ads-ctr-{name[:20]}",
                    "type": "ad_copy",
                    "priority": "high",
                    "title": f"Improve CTR for '{name}' ({ctr:.1f}%)",
                    "description": f"CTR {ctr:.2f}% is below 1% threshold with {impressions} impressions.",
                    "action": "Generate new RSA variants with better headlines and descriptions",
                    "campaign": name,
                    "status": "pending"
                })
            
            # No conversions but spending
            if conversions == 0 and cost > 50:
                actions.append({
                    "id": f"ads-noconv-{name[:20]}",
                    "type": "budget",
                    "priority": "critical",
                    "title": f"No conversions: '{name}' (${cost:.0f} spent)",
                    "description": f"${cost:.2f} spent with 0 conversions. Consider pausing or restructuring.",
                    "action": "Pause campaign or reallocate budget to top performers",
                    "campaign": name,
                    "status": "pending"
                })
            
            # Budget underspend
            budget = camp.get("budget", 0)
            if budget > 0 and cost < budget * 0.5 and impressions < 100:
                actions.append({
                    "id": f"ads-underspend-{name[:20]}",
                    "type": "budget",
                    "priority": "medium",
                    "title": f"Budget underspend: '{name}'",
                    "description": f"Only ${cost:.2f} of ${budget:.2f} daily budget used. Low impression share.",
                    "action": "Broaden keyword targeting or increase bids to capture more traffic",
                    "campaign": name,
                    "status": "pending"
                })
    except Exception as e:
        campaign_data = [{"error": str(e)}]
    
    # 2. Keyword-level analysis
    try:
        keyword_data = await ads_agent.get_keyword_performance(date_range=14)
        for kw in keyword_data:
            qs = kw.get("quality_score")
            keyword = kw.get("keyword", "")
            ctr = kw.get("ctr", 0)
            impressions = kw.get("impressions", 0)
            
            if qs is not None and qs < 5:
                actions.append({
                    "id": f"ads-qs-{keyword[:20]}",
                    "type": "quality_score",
                    "priority": "high",
                    "title": f"Low Quality Score: '{keyword}' (QS={qs})",
                    "description": f"Quality Score {qs}/10 increases CPC and reduces ad rank.",
                    "action": "Improve ad relevance, landing page experience, and expected CTR",
                    "keyword": keyword,
                    "campaign": kw.get("campaign", ""),
                    "status": "pending"
                })
            
            if ctr < 0.5 and impressions >= 500:
                actions.append({
                    "id": f"ads-pause-{keyword[:20]}",
                    "type": "keyword_management",
                    "priority": "medium",
                    "title": f"Pause underperformer: '{keyword}'",
                    "description": f"CTR {ctr:.2f}% after {impressions} impressions. Wasting budget.",
                    "action": "Pause keyword and redirect budget to better performers",
                    "keyword": keyword,
                    "status": "pending"
                })
    except Exception as e:
        keyword_data = [{"error": str(e)}]
    
    # 3. Negative keyword opportunities
    try:
        search_terms = await ads_agent.get_search_terms_report(date_range=7)
        neg_candidates = [t for t in search_terms if t.get("ctr", 0) < 0.3 and t.get("conversions", 0) == 0 and t.get("impressions", 0) >= 100]
        if neg_candidates:
            actions.append({
                "id": "ads-negatives",
                "type": "negative_keywords",
                "priority": "medium",
                "title": f"Add {len(neg_candidates)} negative keywords",
                "description": f"Found {len(neg_candidates)} search terms with <0.3% CTR and 0 conversions.",
                "action": "Add these terms as negative keywords to stop wasted spend",
                "terms": [t.get("search_term", "") for t in neg_candidates[:10]],
                "status": "pending"
            })
    except Exception as e:
        search_terms = [{"error": str(e)}]
    
    # 4. Check for missing campaign types
    existing_names = [c.get("name", "").lower() for c in campaign_data if isinstance(c, dict) and "name" in c]
    for camp_type, config in ads_agent.CAMPAIGN_STRUCTURE.items():
        if not any(config["name"].lower() in n for n in existing_names):
            actions.append({
                "id": f"ads-create-{camp_type}",
                "type": "campaign_creation",
                "priority": "medium",
                "title": f"Create missing campaign: {config['name']}",
                "description": f"Campaign type '{camp_type}' not found in Google Ads account.",
                "action": f"Create {config['name']} campaign with keywords: {', '.join(config['keywords'][:3])}",
                "campaign_type": camp_type,
                "status": "pending"
            })
    
    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
    
    return {
        "success": True,
        "summary": {
            "total_actions": len(actions),
            "critical": sum(1 for a in actions if a["priority"] == "critical"),
            "high": sum(1 for a in actions if a["priority"] == "high"),
            "medium": sum(1 for a in actions if a["priority"] == "medium"),
            "campaigns_analyzed": len([c for c in campaign_data if isinstance(c, dict) and "name" in c]),
            "keywords_analyzed": len([k for k in keyword_data if isinstance(k, dict) and "keyword" in k]),
        },
        "campaigns": [c for c in campaign_data if isinstance(c, dict) and "name" in c],
        "actions": actions
    }


@router.post("/execute")
async def execute_ads_action(action: Dict[str, Any] = Body(...)):
    """Execute an Ads action"""
    if not action:
        raise HTTPException(status_code=400, detail="No action provided")
    
    action_type = action.get("type", "")
    campaign = action.get("campaign", "")
    keyword = action.get("keyword", "")
    
    try:
        if action_type == "ad_copy":
            # Generate new RSA variants
            result = await ads_agent.generate_rsa(
                campaign=campaign,
                ad_group=campaign,
                target_keyword=keyword or None
            )
            return {
                "success": True,
                "action_type": "rsa_generated",
                "campaign": campaign,
                "result": result
            }
        
        elif action_type == "bid_optimization":
            result = await ads_agent.optimize_bids()
            return {
                "success": True,
                "action_type": "bids_optimized",
                "result": result
            }
        
        elif action_type == "negative_keywords":
            terms = action.get("terms", [])
            result = await ads_agent.harvest_negative_keywords()
            return {
                "success": True,
                "action_type": "negatives_harvested",
                "count": len(result),
                "keywords": result[:20]
            }
        
        elif action_type == "campaign_creation":
            camp_type = action.get("campaign_type", "")
            if camp_type:
                result = await ads_agent.create_campaign(camp_type)
                return {
                    "success": True,
                    "action_type": "campaign_created",
                    "result": result
                }
            return {"success": False, "message": "No campaign_type specified"}
        
        elif action_type == "quality_score":
            # Generate AI suggestions for improving QS
            if ads_agent.client:
                response = ads_agent.client.messages.create(
                    model=ads_agent.model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": f"""For the Google Ads keyword '{keyword}' in campaign '{campaign}' for Successifier (customer success platform), provide specific recommendations to improve Quality Score:

1. Ad relevance improvements (specific headline/description suggestions)
2. Landing page improvements
3. Expected CTR improvements
4. Keyword match type recommendations

Be specific and actionable."""}]
                )
                return {
                    "success": True,
                    "action_type": "qs_recommendations",
                    "keyword": keyword,
                    "suggestions": response.content[0].text
                }
            return {
                "success": True,
                "action_type": "qs_recommendations",
                "keyword": keyword,
                "suggestions": f"1. Add '{keyword}' to ad headlines\n2. Create dedicated landing page\n3. Use exact match for better relevance\n4. Improve page load speed"
            }
        
        elif action_type in ("budget", "keyword_management"):
            return {
                "success": True,
                "action_type": "flagged",
                "message": f"Action flagged for manual review: {action.get('title', action_type)}"
            }
        
        else:
            return {"success": False, "message": f"Unknown action type: {action_type}"}
    
    except Exception as e:
        return {"success": False, "error": str(e)}
