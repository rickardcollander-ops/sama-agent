"""
Meta Ads Agent - Campaign Management and Optimization
Uses Meta Marketing API v21 for Facebook + Instagram campaigns.

Requires stored credentials (ad_platform_credentials table, platform='meta'):
  access_token: System User long-lived token from Meta Business Manager
  account_id:   Ad Account ID (act_XXXXXXXXX or plain number)
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from shared.database import get_supabase

logger = logging.getLogger(__name__)

META_API_BASE = "https://graph.facebook.com/v21.0"


class MetaAdsAgent:
    """Meta Ads Agent for Facebook + Instagram campaign management."""

    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    # ── Credentials ──────────────────────────────────────────────────────────

    async def _get_credentials(self, tenant_id: str) -> Optional[Dict]:
        try:
            sb = get_supabase()
            result = (
                sb.table("ad_platform_credentials")
                .select("access_token, account_id")
                .eq("tenant_id", tenant_id)
                .eq("platform", "meta")
                .eq("is_connected", True)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.warning(f"Meta credentials fetch failed: {e}")
            return None

    @staticmethod
    def _act(account_id: str) -> str:
        """Ensure account_id has act_ prefix."""
        return account_id if account_id.startswith("act_") else f"act_{account_id}"

    # ── API helpers ───────────────────────────────────────────────────────────

    async def _get(self, path: str, params: Dict, token: str) -> Dict:
        try:
            resp = await self.http.get(
                f"{META_API_BASE}/{path}",
                params={"access_token": token, **params},
            )
            if resp.status_code != 200:
                logger.warning(f"Meta API GET {path} → {resp.status_code}: {resp.text[:300]}")
                return {"data": [], "error": resp.text[:300]}
            return resp.json()
        except Exception as e:
            logger.error(f"Meta API GET {path} error: {e}")
            return {"data": [], "error": str(e)}

    async def _post(self, path: str, data: Dict, token: str) -> Dict:
        try:
            resp = await self.http.post(
                f"{META_API_BASE}/{path}",
                data={"access_token": token, **data},
            )
            if resp.status_code != 200:
                logger.warning(f"Meta API POST {path} → {resp.status_code}: {resp.text[:300]}")
                return {"success": False, "error": resp.text[:300]}
            result = resp.json()
            # Meta returns {"success": true} on successful mutations
            return result
        except Exception as e:
            logger.error(f"Meta API POST {path} error: {e}")
            return {"success": False, "error": str(e)}

    # ── Verification ──────────────────────────────────────────────────────────

    async def verify_credentials(self, access_token: str, account_id: str) -> Dict:
        """Verify credentials against Meta API."""
        act_id = self._act(account_id)
        result = await self._get(
            act_id,
            {"fields": "id,name,currency,account_status,timezone_name"},
            access_token,
        )
        if result.get("id"):
            status_map = {
                1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED",
                7: "PENDING_RISK_REVIEW", 8: "PENDING_SETTLEMENT",
                9: "IN_GRACE_PERIOD", 100: "PENDING_CLOSURE",
                101: "CLOSED",
            }
            return {
                "valid": True,
                "account_name": result.get("name", ""),
                "currency": result.get("currency", ""),
                "timezone": result.get("timezone_name", ""),
                "account_status": status_map.get(
                    result.get("account_status"), str(result.get("account_status"))
                ),
            }
        error = result.get("error", {}) if isinstance(result.get("error"), dict) else result.get("error", "Unknown error")
        return {"valid": False, "error": str(error)}

    # ── Campaigns ─────────────────────────────────────────────────────────────

    async def get_campaigns(self, tenant_id: str, date_range: int = 30) -> List[Dict]:
        """Fetch campaigns with performance insights from Meta API."""
        creds = await self._get_credentials(tenant_id)
        if not creds:
            return []

        token = creds["access_token"]
        act_id = self._act(creds["account_id"])
        since = (datetime.utcnow() - timedelta(days=date_range)).strftime("%Y-%m-%d")
        until = datetime.utcnow().strftime("%Y-%m-%d")
        time_range = json.dumps({"since": since, "until": until})

        # Fetch campaign list
        camps_result = await self._get(
            f"{act_id}/campaigns",
            {
                "fields": "id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time,created_time",
                "limit": 100,
            },
            token,
        )
        campaigns_raw = camps_result.get("data", [])
        if not campaigns_raw:
            return []

        # Batch fetch insights for all campaigns
        insights_result = await self._get(
            f"{act_id}/insights",
            {
                "fields": "campaign_id,impressions,clicks,spend,actions,ctr,frequency,reach",
                "level": "campaign",
                "time_range": time_range,
                "limit": 200,
            },
            token,
        )
        insights_by_id: Dict[str, Dict] = {
            ins.get("campaign_id", ""): ins
            for ins in insights_result.get("data", [])
        }

        enriched = []
        for camp in campaigns_raw:
            camp_id = camp["id"]
            ins = insights_by_id.get(camp_id, {})

            spend = float(ins.get("spend", 0))
            clicks = int(ins.get("clicks", 0))
            impressions = int(ins.get("impressions", 0))
            reach = int(ins.get("reach", 0))
            # Meta returns ctr as percentage string e.g. "2.3456"
            ctr = float(ins.get("ctr", 0))
            frequency = float(ins.get("frequency", 0))

            actions = ins.get("actions", [])
            leads = sum(
                int(float(a.get("value", 0)))
                for a in actions
                if a.get("action_type") in ("lead", "offsite_conversion.lead", "leadgen_grouped")
            )
            conversions = sum(
                int(float(a.get("value", 0)))
                for a in actions
                if "purchase" in a.get("action_type", "")
            )

            daily_budget_raw = camp.get("daily_budget")
            lifetime_budget_raw = camp.get("lifetime_budget")

            enriched.append({
                "id": camp_id,
                "name": camp.get("name", ""),
                "status": camp.get("status", "UNKNOWN"),
                "objective": camp.get("objective", ""),
                "daily_budget": round(int(daily_budget_raw) / 100, 2) if daily_budget_raw else None,
                "lifetime_budget": round(int(lifetime_budget_raw) / 100, 2) if lifetime_budget_raw else None,
                "start_time": camp.get("start_time"),
                "stop_time": camp.get("stop_time"),
                "created_time": camp.get("created_time"),
                "spend": round(spend, 2),
                "impressions": impressions,
                "reach": reach,
                "clicks": clicks,
                "ctr": round(ctr, 4),
                "frequency": round(frequency, 2),
                "leads": leads,
                "conversions": conversions,
                "cpl": round(spend / leads, 2) if leads > 0 else 0,
                "cpc": round(spend / clicks, 2) if clicks > 0 else 0,
            })

        enriched.sort(key=lambda c: c["spend"], reverse=True)
        return enriched

    async def get_performance_summary(self, tenant_id: str, date_range: int = 30) -> Dict:
        """Aggregate performance summary across all campaigns."""
        campaigns = await self.get_campaigns(tenant_id, date_range)
        if not campaigns:
            return {"configured": False, "campaigns": []}

        total_spend = sum(c["spend"] for c in campaigns)
        total_impressions = sum(c["impressions"] for c in campaigns)
        total_clicks = sum(c["clicks"] for c in campaigns)
        total_leads = sum(c["leads"] for c in campaigns)
        total_reach = sum(c["reach"] for c in campaigns)

        return {
            "configured": True,
            "date_range": date_range,
            "summary": {
                "total_spend": round(total_spend, 2),
                "total_impressions": total_impressions,
                "total_clicks": total_clicks,
                "total_leads": total_leads,
                "total_reach": total_reach,
                "blended_cpl": round(total_spend / total_leads, 2) if total_leads > 0 else 0,
                "blended_ctr": round(total_clicks / total_impressions * 100, 4) if total_impressions > 0 else 0,
                "blended_cpc": round(total_spend / total_clicks, 2) if total_clicks > 0 else 0,
                "active_campaigns": sum(1 for c in campaigns if c["status"] == "ACTIVE"),
                "paused_campaigns": sum(1 for c in campaigns if c["status"] == "PAUSED"),
            },
            "campaigns": campaigns,
        }

    # ── Ad Sets ───────────────────────────────────────────────────────────────

    async def get_adsets(self, tenant_id: str, campaign_id: str, date_range: int = 30) -> List[Dict]:
        """Fetch ad sets for a campaign with performance insights."""
        creds = await self._get_credentials(tenant_id)
        if not creds:
            return []

        token = creds["access_token"]
        since = (datetime.utcnow() - timedelta(days=date_range)).strftime("%Y-%m-%d")
        until = datetime.utcnow().strftime("%Y-%m-%d")
        time_range = json.dumps({"since": since, "until": until})

        adsets_result = await self._get(
            f"{campaign_id}/adsets",
            {
                "fields": "id,name,status,daily_budget,optimization_goal,bid_strategy",
                "limit": 50,
            },
            token,
        )
        adsets_raw = adsets_result.get("data", [])
        if not adsets_raw:
            return []

        # Batch insights at adset level for this campaign
        insights_result = await self._get(
            f"{campaign_id}/insights",
            {
                "fields": "adset_id,impressions,clicks,spend,actions,ctr,frequency,reach",
                "level": "adset",
                "time_range": time_range,
                "limit": 100,
            },
            token,
        )
        insights_by_id = {
            ins.get("adset_id", ""): ins
            for ins in insights_result.get("data", [])
        }

        enriched = []
        for adset in adsets_raw:
            adset_id = adset["id"]
            ins = insights_by_id.get(adset_id, {})
            spend = float(ins.get("spend", 0))
            leads = sum(
                int(float(a.get("value", 0)))
                for a in ins.get("actions", [])
                if a.get("action_type") in ("lead", "offsite_conversion.lead", "leadgen_grouped")
            )
            daily_budget_raw = adset.get("daily_budget")
            enriched.append({
                "id": adset_id,
                "name": adset.get("name", ""),
                "status": adset.get("status", ""),
                "daily_budget": round(int(daily_budget_raw) / 100, 2) if daily_budget_raw else None,
                "optimization_goal": adset.get("optimization_goal", ""),
                "bid_strategy": adset.get("bid_strategy", ""),
                "spend": round(spend, 2),
                "impressions": int(ins.get("impressions", 0)),
                "clicks": int(ins.get("clicks", 0)),
                "reach": int(ins.get("reach", 0)),
                "frequency": float(ins.get("frequency", 0)),
                "leads": leads,
                "cpl": round(spend / leads, 2) if leads > 0 else 0,
            })

        return enriched

    # ── Campaign Actions ──────────────────────────────────────────────────────

    async def set_campaign_status(self, tenant_id: str, campaign_id: str, status: str) -> Dict:
        """Set campaign status to ACTIVE or PAUSED."""
        if status not in ("ACTIVE", "PAUSED"):
            return {"success": False, "error": f"Invalid status: {status}"}
        creds = await self._get_credentials(tenant_id)
        if not creds:
            return {"success": False, "error": "No Meta credentials stored for this account"}
        result = await self._post(campaign_id, {"status": status}, creds["access_token"])
        if result.get("success") is False and result.get("error"):
            return result
        return {"success": True, "campaign_id": campaign_id, "new_status": status}

    async def update_campaign_budget(self, tenant_id: str, campaign_id: str, daily_budget: float) -> Dict:
        """Update campaign daily budget. daily_budget in account currency (e.g. SEK or USD)."""
        creds = await self._get_credentials(tenant_id)
        if not creds:
            return {"success": False, "error": "No Meta credentials stored for this account"}
        # Meta Marketing API takes budget in smallest currency unit (cents / öre)
        budget_cents = int(round(daily_budget * 100))
        result = await self._post(
            campaign_id,
            {"daily_budget": str(budget_cents)},
            creds["access_token"],
        )
        if result.get("success") is False and result.get("error"):
            return result
        return {
            "success": True,
            "campaign_id": campaign_id,
            "new_daily_budget": daily_budget,
        }

    # ── AI Insights ───────────────────────────────────────────────────────────

    async def generate_ai_insights(self, tenant_id: str, anthropic_client=None) -> Dict:
        """Generate Claude-powered insights for Meta campaign performance."""
        campaigns = await self.get_campaigns(tenant_id, date_range=14)
        if not campaigns:
            return {"configured": False, "insights": [], "message": "No Meta campaigns found"}

        if not anthropic_client:
            return {"configured": True, "insights": [], "message": "No AI client configured"}

        summary = [
            {
                "name": c["name"],
                "status": c["status"],
                "spend": c["spend"],
                "impressions": c["impressions"],
                "clicks": c["clicks"],
                "leads": c["leads"],
                "cpl": c["cpl"],
                "ctr": c["ctr"],
                "frequency": c["frequency"],
            }
            for c in campaigns[:15]
        ]

        prompt = f"""Analyze these Meta Ads campaigns (last 14 days) and return exactly 5 actionable insights as JSON.

Campaign data:
{json.dumps(summary, indent=2)}

Rules:
- frequency > 3.5 = creative fatigue, flag it
- CPL > 2x the account average = underperformer
- CTR < 1% = low engagement
- Campaigns with 0 leads but significant spend = critical issue
- PAUSED campaigns with recent spend history = worth investigating

Return only valid JSON:
{{"insights": [
  {{
    "title": "Short descriptive title",
    "observation": "What the data shows",
    "action": "Specific action to take",
    "priority": "high|medium|low",
    "campaign": "Campaign name or null"
  }}
]}}"""

        try:
            def _call():
                return anthropic_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": prompt}],
                    system="You are a Meta Ads optimization expert. Return only valid JSON, no markdown.",
                )

            response = await asyncio.to_thread(_call)
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            data = json.loads(raw)
            return {"configured": True, "insights": data.get("insights", [])}
        except Exception as e:
            logger.error(f"Meta AI insights error: {e}")
            return {"configured": True, "insights": [], "error": str(e)}


meta_ads_agent = MetaAdsAgent()
