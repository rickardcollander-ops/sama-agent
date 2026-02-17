"""
Advanced Google Ads Features
Performance Max campaigns, device bid adjustments, ad copy rotation analysis
"""

from typing import Dict, Any, List, Optional
import httpx
import logging
from datetime import datetime, timedelta

from shared.config import settings
from shared.database import get_supabase
from shared.alerts import alert_system, Alert, AlertType, AlertSeverity

logger = logging.getLogger(__name__)


class AdvancedAdsManager:
    """Advanced Google Ads campaign management"""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.sb = None
        self.customer_id = settings.GOOGLE_ADS_CUSTOMER_ID.replace("-", "")
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def _get_access_token(self) -> str:
        """Get OAuth2 access token"""
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "client_id": settings.GOOGLE_ADS_CLIENT_ID or settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_ADS_CLIENT_SECRET or settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": settings.GOOGLE_ADS_REFRESH_TOKEN or settings.GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token"
        }
        
        response = await self.http_client.post(token_url, data=data)
        response.raise_for_status()
        return response.json()["access_token"]
    
    async def create_performance_max_campaign(
        self,
        campaign_name: str,
        budget: float,
        target_roas: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Create Performance Max campaign
        
        Args:
            campaign_name: Campaign name
            budget: Daily budget in USD
            target_roas: Optional target ROAS (e.g., 3.0 for 300%)
        """
        try:
            access_token = await self._get_access_token()
            
            # Performance Max campaign settings
            campaign_data = {
                "name": campaign_name,
                "advertisingChannelType": "PERFORMANCE_MAX",
                "status": "PAUSED",  # Start paused for review
                "biddingStrategyType": "MAXIMIZE_CONVERSION_VALUE" if target_roas else "MAXIMIZE_CONVERSIONS",
                "campaignBudget": {
                    "amountMicros": int(budget * 1_000_000),
                    "deliveryMethod": "STANDARD"
                },
                "targetRoas": target_roas if target_roas else None
            }
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                "Content-Type": "application/json"
            }
            
            url = f"https://googleads.googleapis.com/v16/customers/{self.customer_id}/campaigns:mutate"
            
            response = await self.http_client.post(
                url,
                headers=headers,
                json={"operations": [{"create": campaign_data}]}
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Log to database
                sb = self._get_sb()
                sb.table("campaigns").insert({
                    "name": campaign_name,
                    "type": "performance_max",
                    "budget": budget,
                    "target_roas": target_roas,
                    "status": "paused",
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
                
                return {
                    "success": True,
                    "campaign_name": campaign_name,
                    "campaign_id": result.get("results", [{}])[0].get("resourceName"),
                    "message": "Performance Max campaign created (paused for review)"
                }
            else:
                return {
                    "success": False,
                    "error": response.text
                }
                
        except Exception as e:
            logger.error(f"Failed to create Performance Max campaign: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def set_device_bid_adjustments(
        self,
        campaign_id: str,
        mobile_adjustment: float,
        tablet_adjustment: float,
        desktop_adjustment: float = 1.0
    ) -> Dict[str, Any]:
        """
        Set device-specific bid adjustments
        
        Args:
            campaign_id: Campaign resource name
            mobile_adjustment: Bid multiplier for mobile (e.g., 1.2 = +20%)
            tablet_adjustment: Bid multiplier for tablet
            desktop_adjustment: Bid multiplier for desktop (default 1.0)
        """
        try:
            access_token = await self._get_access_token()
            
            adjustments = [
                {
                    "device": "MOBILE",
                    "bidModifier": mobile_adjustment
                },
                {
                    "device": "TABLET",
                    "bidModifier": tablet_adjustment
                },
                {
                    "device": "DESKTOP",
                    "bidModifier": desktop_adjustment
                }
            ]
            
            operations = []
            for adj in adjustments:
                operations.append({
                    "create": {
                        "campaign": campaign_id,
                        "device": adj["device"],
                        "bidModifier": adj["bidModifier"]
                    }
                })
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                "Content-Type": "application/json"
            }
            
            url = f"https://googleads.googleapis.com/v16/customers/{self.customer_id}/campaignCriteria:mutate"
            
            response = await self.http_client.post(
                url,
                headers=headers,
                json={"operations": operations}
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": "Device bid adjustments set",
                    "adjustments": adjustments
                }
            else:
                return {
                    "success": False,
                    "error": response.text
                }
                
        except Exception as e:
            logger.error(f"Failed to set device bid adjustments: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def analyze_ad_copy_rotation(self, ad_group_id: str) -> Dict[str, Any]:
        """
        Analyze ad copy performance to identify winners
        
        Args:
            ad_group_id: Ad group resource name
        """
        try:
            access_token = await self._get_access_token()
            
            # Query for ad performance
            query = f"""
                SELECT
                    ad_group_ad.ad.id,
                    ad_group_ad.ad.responsive_search_ad.headlines,
                    ad_group_ad.ad.responsive_search_ad.descriptions,
                    metrics.impressions,
                    metrics.clicks,
                    metrics.ctr,
                    metrics.conversions,
                    metrics.cost_micros
                FROM ad_group_ad
                WHERE ad_group_ad.ad_group = '{ad_group_id}'
                    AND segments.date DURING LAST_30_DAYS
                ORDER BY metrics.impressions DESC
            """
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                "Content-Type": "application/json"
            }
            
            url = f"https://googleads.googleapis.com/v16/customers/{self.customer_id}/googleAds:search"
            
            response = await self.http_client.post(
                url,
                headers=headers,
                json={"query": query}
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": response.text
                }
            
            results = response.json().get("results", [])
            
            # Analyze performance
            ads = []
            for result in results:
                ad = result.get("adGroupAd", {}).get("ad", {})
                metrics = result.get("metrics", {})
                
                impressions = int(metrics.get("impressions", 0))
                clicks = int(metrics.get("clicks", 0))
                ctr = float(metrics.get("ctr", 0))
                conversions = float(metrics.get("conversions", 0))
                cost = int(metrics.get("costMicros", 0)) / 1_000_000
                
                ads.append({
                    "ad_id": ad.get("id"),
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": ctr,
                    "conversions": conversions,
                    "cost": cost,
                    "cpa": cost / conversions if conversions > 0 else 0
                })
            
            # Identify winner (highest CTR with significant impressions)
            winner = None
            if ads:
                significant_ads = [a for a in ads if a["impressions"] >= 100]
                if significant_ads:
                    winner = max(significant_ads, key=lambda x: x["ctr"])
            
            # Identify underperformers
            avg_ctr = sum(a["ctr"] for a in ads) / len(ads) if ads else 0
            underperformers = [a for a in ads if a["ctr"] < avg_ctr * 0.5 and a["impressions"] >= 100]
            
            return {
                "success": True,
                "total_ads": len(ads),
                "winner": winner,
                "avg_ctr": avg_ctr,
                "underperformers": underperformers,
                "recommendation": "Pause underperforming ads and create variations of winner" if winner else "Need more data"
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze ad copy rotation: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def check_cpc_spikes(self) -> List[Alert]:
        """Check for CPC spikes and create alerts"""
        try:
            access_token = await self._get_access_token()
            
            # Get CPC data for last 7 days vs previous 7 days
            query = """
                SELECT
                    campaign.name,
                    metrics.average_cpc,
                    segments.date
                FROM campaign
                WHERE segments.date DURING LAST_14_DAYS
            """
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                "Content-Type": "application/json"
            }
            
            url = f"https://googleads.googleapis.com/v16/customers/{self.customer_id}/googleAds:search"
            
            response = await self.http_client.post(
                url,
                headers=headers,
                json={"query": query}
            )
            
            if response.status_code != 200:
                return []
            
            results = response.json().get("results", [])
            
            # Group by campaign and calculate averages
            from collections import defaultdict
            campaign_data = defaultdict(lambda: {"recent": [], "previous": []})
            
            cutoff = datetime.utcnow() - timedelta(days=7)
            
            for result in results:
                campaign_name = result.get("campaign", {}).get("name")
                cpc_micros = int(result.get("metrics", {}).get("averageCpc", 0))
                cpc = cpc_micros / 1_000_000
                date_str = result.get("segments", {}).get("date")
                date = datetime.strptime(date_str, "%Y-%m-%d")
                
                if date >= cutoff:
                    campaign_data[campaign_name]["recent"].append(cpc)
                else:
                    campaign_data[campaign_name]["previous"].append(cpc)
            
            # Check for spikes
            alerts = []
            for campaign, data in campaign_data.items():
                if data["recent"] and data["previous"]:
                    avg_recent = sum(data["recent"]) / len(data["recent"])
                    avg_previous = sum(data["previous"]) / len(data["previous"])
                    
                    alert = await alert_system.check_cpc_spike(avg_recent, avg_previous)
                    if alert:
                        alert.data["campaign"] = campaign
                        await alert_system.send_alert(alert)
                        alerts.append(alert)
            
            return alerts
            
        except Exception as e:
            logger.error(f"Failed to check CPC spikes: {e}")
            return []


# Global instance
advanced_ads_manager = AdvancedAdsManager()
