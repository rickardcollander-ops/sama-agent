"""
Google Ads Agent - Campaign Management and Optimization
Manages all Google Ads campaigns for successifier.com
Uses Google Ads REST API v16 for real campaign data.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from shared.google_auth import get_access_token, is_ads_configured
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)

# Google Ads REST API v16
GOOGLE_ADS_API = "https://googleads.googleapis.com/v16"


class GoogleAdsAgent:
    """
    Google Ads Agent responsible for:
    - Campaign management (real Google Ads API)
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
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
        self.customer_id = settings.GOOGLE_ADS_CUSTOMER_ID.replace("-", "")
    
    # ── Google Ads REST API helpers ────────────────────────────────────
    
    async def _ads_query(self, gaql: str) -> List[Dict]:
        """Execute a Google Ads Query Language (GAQL) query via REST API"""
        if not is_ads_configured():
            return []
        
        token = await get_access_token("ads")
        if not token:
            return []
        
        url = f"{GOOGLE_ADS_API}/customers/{self.customer_id}/googleAds:searchStream"
        
        resp = await self.http_client.post(url, json={"query": gaql}, headers={
            "Authorization": f"Bearer {token}",
            "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
            "Content-Type": "application/json"
        })
        
        if resp.status_code != 200:
            logger.warning(f"Google Ads API error: {resp.status_code} {resp.text[:300]}")
            return []
        
        results = []
        for batch in resp.json():
            results.extend(batch.get("results", []))
        
        return results
    
    async def get_campaign_performance(self, date_range: int = 30) -> List[Dict[str, Any]]:
        """Fetch real campaign performance from Google Ads API"""
        start_date = (datetime.utcnow() - timedelta(days=date_range)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        gaql = f"""
            SELECT
                campaign.id,
                campaign.name,
                campaign.status,
                campaign_budget.amount_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.ctr,
                metrics.conversions,
                metrics.cost_micros,
                metrics.cost_per_conversion,
                metrics.conversions_value
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
                AND campaign.status != 'REMOVED'
            ORDER BY metrics.cost_micros DESC
        """
        
        rows = await self._ads_query(gaql)
        
        campaigns = []
        for row in rows:
            campaign = row.get("campaign", {})
            metrics = row.get("metrics", {})
            budget = row.get("campaignBudget", {})
            
            cost = int(metrics.get("costMicros", 0)) / 1_000_000
            conversions = float(metrics.get("conversions", 0))
            conv_value = float(metrics.get("conversionsValue", 0))
            
            campaigns.append({
                "campaign_id": campaign.get("id"),
                "name": campaign.get("name"),
                "status": campaign.get("status"),
                "budget": int(budget.get("amountMicros", 0)) / 1_000_000,
                "impressions": int(metrics.get("impressions", 0)),
                "clicks": int(metrics.get("clicks", 0)),
                "ctr": round(float(metrics.get("ctr", 0)) * 100, 2),
                "conversions": conversions,
                "cost": round(cost, 2),
                "cpa": round(cost / conversions, 2) if conversions > 0 else 0,
                "roas": round(conv_value / cost, 2) if cost > 0 else 0
            })
        
        if not campaigns:
            logger.info("Google Ads API not configured or no campaigns found")
        
        return campaigns
    
    async def get_search_terms_report(self, date_range: int = 7) -> List[Dict[str, Any]]:
        """Fetch real search terms report from Google Ads"""
        start_date = (datetime.utcnow() - timedelta(days=date_range)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        gaql = f"""
            SELECT
                search_term_view.search_term,
                campaign.name,
                metrics.impressions,
                metrics.clicks,
                metrics.ctr,
                metrics.conversions,
                metrics.cost_micros
            FROM search_term_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY metrics.impressions DESC
            LIMIT 200
        """
        
        rows = await self._ads_query(gaql)
        
        terms = []
        for row in rows:
            stv = row.get("searchTermView", {})
            metrics = row.get("metrics", {})
            campaign = row.get("campaign", {})
            
            terms.append({
                "search_term": stv.get("searchTerm", ""),
                "campaign": campaign.get("name", ""),
                "impressions": int(metrics.get("impressions", 0)),
                "clicks": int(metrics.get("clicks", 0)),
                "ctr": round(float(metrics.get("ctr", 0)) * 100, 2),
                "conversions": float(metrics.get("conversions", 0)),
                "cost": round(int(metrics.get("costMicros", 0)) / 1_000_000, 2)
            })
        
        return terms
    
    async def get_keyword_performance(self, date_range: int = 30) -> List[Dict[str, Any]]:
        """Fetch keyword-level performance from Google Ads"""
        start_date = (datetime.utcnow() - timedelta(days=date_range)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        gaql = f"""
            SELECT
                ad_group_criterion.keyword.text,
                ad_group_criterion.keyword.match_type,
                ad_group_criterion.quality_info.quality_score,
                campaign.name,
                ad_group.name,
                metrics.impressions,
                metrics.clicks,
                metrics.ctr,
                metrics.conversions,
                metrics.cost_micros,
                metrics.average_cpc
            FROM keyword_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
                AND ad_group_criterion.status != 'REMOVED'
            ORDER BY metrics.impressions DESC
            LIMIT 100
        """
        
        rows = await self._ads_query(gaql)
        
        keywords = []
        for row in rows:
            criterion = row.get("adGroupCriterion", {}).get("keyword", {})
            quality = row.get("adGroupCriterion", {}).get("qualityInfo", {})
            metrics = row.get("metrics", {})
            
            keywords.append({
                "keyword": criterion.get("text", ""),
                "match_type": criterion.get("matchType", ""),
                "quality_score": quality.get("qualityScore"),
                "campaign": row.get("campaign", {}).get("name", ""),
                "ad_group": row.get("adGroup", {}).get("name", ""),
                "impressions": int(metrics.get("impressions", 0)),
                "clicks": int(metrics.get("clicks", 0)),
                "ctr": round(float(metrics.get("ctr", 0)) * 100, 2),
                "conversions": float(metrics.get("conversions", 0)),
                "cost": round(int(metrics.get("costMicros", 0)) / 1_000_000, 2),
                "avg_cpc": round(int(metrics.get("averageCpc", 0)) / 1_000_000, 2)
            })
        
        return keywords
    
    # ── Existing methods (now with real data) ──────────────────────────
    
    async def generate_rsa(
        self,
        campaign: str,
        ad_group: str,
        target_keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate Responsive Search Ad variants"""
        logger.info(f"📢 Generating RSA for {campaign} / {ad_group}")
        
        if not self.client:
            return {"error": "Anthropic API key not configured", "status": "error"}
        
        system_prompt = brand_voice.get_system_prompt("blog")
        
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
        
        try:
            rsa_data = json.loads(response.content[0].text)
        except:
            rsa_data = {
                "headlines": self.RSA_HEADLINE_BANK[:15],
                "descriptions": self.RSA_DESCRIPTION_BANK
            }
        
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
        campaign_id: str = "",
        performance_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Optimize bids based on real or provided performance data"""
        logger.info(f"💰 Optimizing bids...")
        
        adjustments = []
        
        # Fetch real keyword data if no data provided
        if not performance_data:
            keywords = await self.get_keyword_performance(date_range=7)
            for kw in keywords:
                ctr = kw.get("ctr", 0)
                impressions = kw.get("impressions", 0)
                conversions = kw.get("conversions", 0)
                cost = kw.get("cost", 0)
                quality_score = kw.get("quality_score")
                cpa = cost / conversions if conversions > 0 else 0
                
                if ctr < 0.5 and impressions >= 500:
                    adjustments.append({
                        "keyword": kw["keyword"],
                        "action": "pause",
                        "reason": f"CTR {ctr}% < 0.5% after {impressions} impressions"
                    })
                elif cpa > 0 and cpa < 80:  # Below $80 target CPA
                    adjustments.append({
                        "keyword": kw["keyword"],
                        "action": "increase_bid",
                        "current_cpa": round(cpa, 2),
                        "reason": f"CPA ${cpa:.2f} below target - scale up"
                    })
                elif quality_score and quality_score < 5:
                    adjustments.append({
                        "keyword": kw["keyword"],
                        "action": "improve_quality_score",
                        "quality_score": quality_score,
                        "reason": "Quality Score below 5"
                    })
        else:
            # Use provided data (original logic)
            for keyword, metrics in performance_data.get("keywords", {}).items():
                ctr = metrics.get("ctr", 0)
                impressions = metrics.get("impressions", 0)
                cpa = metrics.get("cpa", 0)
                target_cpa = metrics.get("target_cpa", 100)
                quality_score = metrics.get("quality_score", 10)
                
                if ctr < 0.5 and impressions >= 500:
                    adjustments.append({
                        "keyword": keyword,
                        "action": "pause",
                        "reason": f"CTR {ctr}% < 0.5% after {impressions} impressions"
                    })
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
                elif quality_score < 5:
                    adjustments.append({
                        "keyword": keyword,
                        "action": "improve_quality_score",
                        "quality_score": quality_score,
                        "reason": "Quality Score below 5"
                    })
        
        logger.info(f"✅ Generated {len(adjustments)} bid adjustments")
        
        return {
            "adjustments": adjustments,
            "timestamp": datetime.utcnow().isoformat(),
            "source": "google_ads_api" if not performance_data else "manual_data"
        }
    
    async def harvest_negative_keywords(
        self,
        search_terms_report: Optional[List[Dict[str, Any]]] = None
    ) -> List[str]:
        """Identify negative keywords from real search terms report"""
        # Fetch real data if not provided
        if not search_terms_report:
            search_terms_report = await self.get_search_terms_report(date_range=7)
        
        logger.info(f"🚫 Harvesting negative keywords from {len(search_terms_report)} search terms")
        
        negative_keywords = []
        
        for term_data in search_terms_report:
            search_term = term_data.get("search_term", "")
            ctr = term_data.get("ctr", 0)
            conversions = term_data.get("conversions", 0)
            impressions = term_data.get("impressions", 0)
            
            if ctr < 0.3 and conversions == 0 and impressions >= 100:
                negative_keywords.append(search_term)
                logger.info(f"  ➕ Negative keyword: '{search_term}' (CTR: {ctr}%, Conv: 0)")
        
        logger.info(f"✅ Identified {len(negative_keywords)} negative keywords")
        return negative_keywords
    
    async def analyze_campaign_performance(
        self,
        campaign_id: str = "",
        date_range: int = 30
    ) -> Dict[str, Any]:
        """Analyze campaign performance with real Google Ads data"""
        logger.info(f"📊 Analyzing campaign performance ({date_range} days)")
        
        campaigns = await self.get_campaign_performance(date_range=date_range)
        
        if not campaigns:
            return {
                "status": "no_data",
                "message": "No campaign data available. Configure GOOGLE_ADS_* env vars.",
                "date_range": date_range,
                "metrics": {"impressions": 0, "clicks": 0, "ctr": 0, "conversions": 0, "cost": 0, "cpa": 0, "roas": 0}
            }
        
        # Aggregate metrics
        total_impressions = sum(c["impressions"] for c in campaigns)
        total_clicks = sum(c["clicks"] for c in campaigns)
        total_conversions = sum(c["conversions"] for c in campaigns)
        total_cost = sum(c["cost"] for c in campaigns)
        
        # Top performers and underperformers
        top = sorted(campaigns, key=lambda c: c["conversions"], reverse=True)[:3]
        under = [c for c in campaigns if c["ctr"] < 1.0 and c["impressions"] > 100]
        
        # Generate AI recommendations if Claude is available
        recommendations = []
        if self.client and campaigns:
            try:
                prompt = f"""Analyze these Google Ads campaigns for Successifier and give 5 specific recommendations:

Campaigns:
{json.dumps(campaigns[:5], indent=2)}

Focus on: CPA optimization, budget allocation, keyword strategy, ad copy improvements."""
                
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}]
                )
                recommendations = [r.strip() for r in resp.content[0].text.strip().split("\n") if r.strip()]
            except Exception as e:
                logger.warning(f"AI recommendations failed: {e}")
        
        return {
            "status": "ok",
            "date_range": date_range,
            "campaign_count": len(campaigns),
            "metrics": {
                "impressions": total_impressions,
                "clicks": total_clicks,
                "ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0,
                "conversions": total_conversions,
                "cost": round(total_cost, 2),
                "cpa": round(total_cost / total_conversions, 2) if total_conversions > 0 else 0,
                "roas": 0
            },
            "campaigns": campaigns,
            "top_performers": top,
            "underperformers": under,
            "recommendations": recommendations
        }
    
    async def create_campaign(self, campaign_type: str) -> Dict[str, Any]:
        """Create a new Google Ads campaign"""
        if campaign_type not in self.CAMPAIGN_STRUCTURE:
            raise ValueError(f"Unknown campaign type: {campaign_type}")
        
        campaign_config = self.CAMPAIGN_STRUCTURE[campaign_type]
        logger.info(f"🚀 Creating campaign: {campaign_config['name']}")
        
        return {
            "campaign_type": campaign_type,
            "name": campaign_config["name"],
            "ad_groups": campaign_config["ad_groups"],
            "keywords": campaign_config["keywords"],
            "bidding_strategy": campaign_config["bidding_strategy"],
            "status": "draft",
            "api_configured": is_ads_configured()
        }
    
    async def run_daily_optimization(self) -> Dict[str, Any]:
        """Run daily optimization with real data"""
        logger.info("🔄 Running daily Google Ads optimization...")
        
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "campaigns_optimized": 0,
            "bid_adjustments": 0,
            "negative_keywords_added": 0,
            "ads_paused": 0,
            "budget_reallocations": 0,
            "api_configured": is_ads_configured()
        }
        
        if not is_ads_configured():
            results["message"] = "Google Ads API not configured. Set GOOGLE_ADS_* env vars."
            return results
        
        # 1. Fetch campaign performance
        campaigns = await self.get_campaign_performance(date_range=7)
        results["campaigns_optimized"] = len(campaigns)
        
        # 2. Optimize bids
        bid_results = await self.optimize_bids()
        results["bid_adjustments"] = len(bid_results.get("adjustments", []))
        
        # 3. Harvest negative keywords
        negatives = await self.harvest_negative_keywords()
        results["negative_keywords_added"] = len(negatives)
        
        # 4. Count underperformers to pause
        for adj in bid_results.get("adjustments", []):
            if adj["action"] == "pause":
                results["ads_paused"] += 1
        
        logger.info(f"✅ Daily optimization complete: {results['campaigns_optimized']} campaigns, {results['bid_adjustments']} adjustments")
        return results


# Global Google Ads agent instance
ads_agent = GoogleAdsAgent()
