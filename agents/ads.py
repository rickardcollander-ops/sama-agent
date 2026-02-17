"""
Google Ads Agent - Campaign Management and Optimization
Manages all Google Ads campaigns for successifier.com
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import AsyncSessionLocal
from shared.event_bus import event_bus
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)


class GoogleAdsAgent:
    """
    Google Ads Agent responsible for:
    - Campaign management
    - RSA (Responsive Search Ads) generation
    - Bid optimization
    - A/B testing
    - Negative keyword harvesting
    - Budget allocation
    """
    
    # Campaign structure from SAMA 2.0 spec
    CAMPAIGN_STRUCTURE = {
        "brand": {
            "name": "Brand Campaign",
            "ad_groups": ["Successifier brand terms"],
            "match_types": ["exact", "phrase"],
            "bidding_strategy": "Target Impression Share",
            "keywords": [
                "successifier",
                "successifier platform",
                "successifier customer success"
            ]
        },
        "core_product": {
            "name": "Core Product - CS Platform",
            "ad_groups": ["Customer success platform", "CS software", "CS tool"],
            "match_types": ["exact", "phrase", "bmm"],
            "bidding_strategy": "Target CPA",
            "keywords": [
                "customer success platform",
                "customer success software",
                "CS platform",
                "customer success tool"
            ]
        },
        "churn_prevention": {
            "name": "Churn Prevention",
            "ad_groups": ["Churn prediction", "Churn reduction"],
            "match_types": ["phrase", "bmm"],
            "bidding_strategy": "Target CPA",
            "keywords": [
                "churn prediction software",
                "reduce SaaS churn",
                "churn reduction tool",
                "customer churn prevention"
            ]
        },
        "health_scoring": {
            "name": "Health Scoring",
            "ad_groups": ["Customer health score", "Account health"],
            "match_types": ["phrase", "bmm"],
            "bidding_strategy": "Target CPA",
            "keywords": [
                "customer health score tool",
                "account health scoring",
                "CS health check"
            ]
        },
        "competitor_conquest": {
            "name": "Competitor Conquest",
            "ad_groups": ["Gainsight alternative", "Totango vs", "ChurnZero pricing"],
            "match_types": ["exact", "phrase"],
            "bidding_strategy": "Manual CPC → Target CPA",
            "keywords": [
                "gainsight alternative",
                "totango alternative",
                "churnzero alternative",
                "gainsight pricing",
                "totango vs gainsight"
            ]
        }
    }
    
    # RSA headline bank (from SAMA 2.0 spec)
    RSA_HEADLINE_BANK = [
        "AI-Native Customer Success Platform",
        "Reduce Churn by 40% With AI",
        "Start Free — No Credit Card Needed",
        "Setup in 30 Minutes. See ROI in 30 Days.",
        "From $79/Month — Cancel Anytime",
        "Predict Churn Before It Happens",
        "3x Faster CS Response Time",
        "Gainsight Alternative at a Fraction of the Cost",
        "25% NRR Improvement Guaranteed",
        "85% Less Manual Work for Your CS Team",
        "Enterprise Features. Startup Pricing.",
        "Built for Small-to-Mid CS Teams",
        "AI-Powered Health Scoring",
        "Automate Your Customer Success",
        "14-Day Free Trial. No Setup Fees."
    ]
    
    RSA_DESCRIPTION_BANK = [
        "AI-native platform that predicts churn, automates onboarding, and guides customers to success. 40% churn reduction, 25% NRR improvement.",
        "Enterprise-grade CS platform at startup pricing. From $79/month. Setup in 30 minutes, see ROI in 30 days. 14-day free trial.",
        "Built for small-to-mid CS teams. AI health scoring, automated playbooks, and customer portals. 85% less manual work.",
        "Better than Gainsight at 1/10th the cost. AI-native, not retrofitted. Perfect for growing SaaS companies."
    ]
    
    # Optimization rules from SAMA 2.0 spec
    OPTIMIZATION_RULES = {
        "pause_underperformer": {
            "condition": "CTR < 0.5% after 500 impressions",
            "action": "pause_keyword_or_ad",
            "schedule": "daily"
        },
        "scale_winner": {
            "condition": "CPA < target by 20%+, ROAS > 300%",
            "action": "increase_bid_by_15_percent",
            "schedule": "daily"
        },
        "quality_score_fix": {
            "condition": "QS < 5 on any keyword",
            "action": "rewrite_ad_copy_and_review_landing_page",
            "schedule": "weekly"
        },
        "budget_reallocation": {
            "condition": "Campaign spend < 70% of budget",
            "action": "shift_budget_to_top_performer",
            "schedule": "weekly"
        },
        "negative_keyword_harvest": {
            "condition": "Search term CTR < 0.3%, 0 conversions",
            "action": "add_to_negative_keyword_list",
            "schedule": "daily"
        }
    }
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
    
    async def generate_rsa(
        self,
        campaign: str,
        ad_group: str,
        target_keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate Responsive Search Ad variants
        
        Args:
            campaign: Campaign name
            ad_group: Ad group name
            target_keyword: Primary keyword to target
        
        Returns:
            RSA with 15 headlines and 4 descriptions
        """
        logger.info(f"📢 Generating RSA for {campaign} / {ad_group}")
        
        system_prompt = brand_voice.get_system_prompt("blog")  # Use brand voice
        
        user_prompt = f"""Generate a Google Ads Responsive Search Ad (RSA) for Successifier.

Campaign: {campaign}
Ad Group: {ad_group}
"""
        
        if target_keyword:
            user_prompt += f"Primary Keyword: {target_keyword}\n"
        
        user_prompt += f"""

Requirements:
- 15 unique headlines (max 30 characters each)
- 4 unique descriptions (max 90 characters each)
- Include Successifier proof points:
  * 40% churn reduction
  * 25% NRR improvement
  * 85% less manual work
  * From $79/month
  * 14-day free trial
- Use action-oriented language
- Include keyword in at least 3 headlines
- Vary messaging (features, benefits, social proof, urgency)

Existing headline examples:
{chr(10).join('- ' + h for h in self.RSA_HEADLINE_BANK[:5])}

Format as JSON:
{{
  "headlines": ["headline 1", "headline 2", ...],
  "descriptions": ["description 1", "description 2", ...]
}}
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        # Parse JSON response
        import json
        try:
            rsa_data = json.loads(response.content[0].text)
        except:
            # Fallback to default headlines/descriptions
            rsa_data = {
                "headlines": self.RSA_HEADLINE_BANK[:15],
                "descriptions": self.RSA_DESCRIPTION_BANK
            }
        
        # Validate character limits
        rsa_data["headlines"] = [h[:30] for h in rsa_data["headlines"][:15]]
        rsa_data["descriptions"] = [d[:90] for d in rsa_data["descriptions"][:4]]
        
        logger.info(f"✅ RSA generated: {len(rsa_data['headlines'])} headlines, {len(rsa_data['descriptions'])} descriptions")
        
        return {
            "campaign": campaign,
            "ad_group": ad_group,
            "target_keyword": target_keyword,
            "headlines": rsa_data["headlines"],
            "descriptions": rsa_data["descriptions"],
            "status": "draft"
        }
    
    async def optimize_bids(
        self,
        campaign_id: str,
        performance_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Optimize bids based on performance data
        
        Args:
            campaign_id: Campaign identifier
            performance_data: Current performance metrics
        
        Returns:
            Bid adjustments to make
        """
        logger.info(f"💰 Optimizing bids for campaign {campaign_id}")
        
        adjustments = []
        
        # Apply optimization rules
        for keyword, metrics in performance_data.get("keywords", {}).items():
            ctr = metrics.get("ctr", 0)
            impressions = metrics.get("impressions", 0)
            cpa = metrics.get("cpa", 0)
            target_cpa = metrics.get("target_cpa", 100)
            quality_score = metrics.get("quality_score", 10)
            
            # Rule: Pause underperformer
            if ctr < 0.5 and impressions >= 500:
                adjustments.append({
                    "keyword": keyword,
                    "action": "pause",
                    "reason": f"CTR {ctr}% < 0.5% after {impressions} impressions"
                })
            
            # Rule: Scale winner
            elif cpa > 0 and cpa < target_cpa * 0.8:
                current_bid = metrics.get("bid", 1.0)
                new_bid = current_bid * 1.15
                adjustments.append({
                    "keyword": keyword,
                    "action": "increase_bid",
                    "current_bid": current_bid,
                    "new_bid": round(new_bid, 2),
                    "reason": f"CPA ${cpa} is 20%+ below target ${target_cpa}"
                })
            
            # Rule: Quality Score fix
            elif quality_score < 5:
                adjustments.append({
                    "keyword": keyword,
                    "action": "improve_quality_score",
                    "quality_score": quality_score,
                    "reason": "Quality Score below 5 - needs ad copy rewrite"
                })
        
        logger.info(f"✅ Generated {len(adjustments)} bid adjustments")
        
        return {
            "campaign_id": campaign_id,
            "adjustments": adjustments,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def harvest_negative_keywords(
        self,
        search_terms_report: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Identify and harvest negative keywords from search terms
        
        Args:
            search_terms_report: Search terms with performance data
        
        Returns:
            List of negative keywords to add
        """
        logger.info(f"🚫 Harvesting negative keywords from {len(search_terms_report)} search terms")
        
        negative_keywords = []
        
        for term_data in search_terms_report:
            search_term = term_data.get("search_term", "")
            ctr = term_data.get("ctr", 0)
            conversions = term_data.get("conversions", 0)
            impressions = term_data.get("impressions", 0)
            
            # Rule: Low CTR, no conversions
            if ctr < 0.3 and conversions == 0 and impressions >= 100:
                negative_keywords.append(search_term)
                logger.info(f"  ➕ Negative keyword: '{search_term}' (CTR: {ctr}%, Conversions: 0)")
        
        logger.info(f"✅ Identified {len(negative_keywords)} negative keywords")
        
        return negative_keywords
    
    async def analyze_campaign_performance(
        self,
        campaign_id: str,
        date_range: int = 30
    ) -> Dict[str, Any]:
        """
        Analyze campaign performance and generate recommendations
        
        Args:
            campaign_id: Campaign identifier
            date_range: Days to analyze
        
        Returns:
            Performance analysis with recommendations
        """
        logger.info(f"📊 Analyzing campaign {campaign_id} performance ({date_range} days)")
        
        # This would fetch real data from Google Ads API
        # For now, return placeholder analysis
        
        analysis = {
            "campaign_id": campaign_id,
            "date_range": date_range,
            "metrics": {
                "impressions": 0,
                "clicks": 0,
                "ctr": 0.0,
                "conversions": 0,
                "cost": 0.0,
                "cpa": 0.0,
                "roas": 0.0
            },
            "recommendations": [],
            "top_performers": [],
            "underperformers": []
        }
        
        return analysis
    
    async def create_campaign(
        self,
        campaign_type: str
    ) -> Dict[str, Any]:
        """
        Create a new Google Ads campaign
        
        Args:
            campaign_type: Type of campaign (brand, core_product, etc.)
        
        Returns:
            Campaign creation result
        """
        if campaign_type not in self.CAMPAIGN_STRUCTURE:
            raise ValueError(f"Unknown campaign type: {campaign_type}")
        
        campaign_config = self.CAMPAIGN_STRUCTURE[campaign_type]
        
        logger.info(f"🚀 Creating campaign: {campaign_config['name']}")
        
        # This would use Google Ads API to create campaign
        # For now, return configuration
        
        return {
            "campaign_type": campaign_type,
            "name": campaign_config["name"],
            "ad_groups": campaign_config["ad_groups"],
            "keywords": campaign_config["keywords"],
            "bidding_strategy": campaign_config["bidding_strategy"],
            "status": "draft",
            "message": "Campaign configuration ready. Use Google Ads API to create."
        }
    
    async def run_daily_optimization(self) -> Dict[str, Any]:
        """
        Run daily optimization routine
        
        Returns:
            Optimization results
        """
        logger.info("🔄 Running daily Google Ads optimization...")
        
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "campaigns_optimized": 0,
            "bid_adjustments": 0,
            "negative_keywords_added": 0,
            "ads_paused": 0,
            "budget_reallocations": 0
        }
        
        # This would:
        # 1. Fetch performance data from Google Ads API
        # 2. Apply optimization rules
        # 3. Make bid adjustments
        # 4. Harvest negative keywords
        # 5. Pause underperformers
        # 6. Reallocate budget
        
        logger.info("✅ Daily optimization complete")
        
        return results


# Global Google Ads agent instance
ads_agent = GoogleAdsAgent()
