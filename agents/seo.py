"""
SEO Agent - Technical SEO, Keyword Tracking, and On-Page Optimization
Handles all SEO activities for successifier.com
Uses Supabase for persistence and real Google APIs where configured.
"""

import asyncio
import logging
import re
from typing import Dict, Any, List, Set
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx
from bs4 import BeautifulSoup

from shared.config import settings
from shared.database import get_supabase
from shared.google_auth import get_access_token, is_gsc_configured
from .models import KEYWORDS_TABLE, SEO_AUDITS_TABLE

logger = logging.getLogger(__name__)

# PageSpeed Insights API (free, no key required for basic usage)
PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Google Search Console API
GSC_API = "https://searchconsole.googleapis.com/webmasters/v3"
# GSC uses sc-domain: format for domain properties (global default)
GSC_SITE_URL = settings.GSC_SITE_URL
# HTTP URL for PageSpeed and technical checks (global default)
SITE_URL = "https://successifier.com"


class SEOAgent:
    """
    SEO Agent responsible for:
    - Technical SEO audits (PageSpeed Insights API)
    - Keyword rank tracking (Google Search Console API)
    - Backlink monitoring
    - On-page optimization
    - Competitor analysis
    """

    COMPETITORS = ["gainsight.com", "totango.com", "churnzero.com"]

    def __init__(self, tenant_config=None):
        self.tenant_config = tenant_config
        api_key = tenant_config.anthropic_api_key if tenant_config else settings.ANTHROPIC_API_KEY
        self.client = Anthropic(api_key=api_key) if api_key else None
        self.model = "claude-sonnet-4-6"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.sb = None
        self.tenant_id = getattr(tenant_config, "tenant_id", "default") if tenant_config else "default"

        # Per-tenant overrides with backward-compatible defaults
        if tenant_config:
            self.gsc_site_url = tenant_config.gsc_site_url
            self.site_url = tenant_config.site_url
            self.competitors = tenant_config.competitors
        else:
            self.gsc_site_url = GSC_SITE_URL
            self.site_url = SITE_URL
            self.competitors = list(self.COMPETITORS)
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def get_keywords(self) -> List[Dict[str, Any]]:
        """Get all tracked keywords from Supabase"""
        sb = self._get_sb()
        result = sb.table(KEYWORDS_TABLE).select("*").execute()
        return result.data or []

    async def run_cycle(self) -> str:
        """Run a standard SEO cycle: track rankings and run audit."""
        rankings = await self.track_keyword_rankings()
        audit = await self.run_weekly_audit()
        improved = len(rankings.get("improved", []))
        declined = len(rankings.get("declined", []))
        critical = len(audit.get("critical_issues", []))
        return f"Tracked {rankings.get('total_keywords', 0)} keywords ({improved} improved, {declined} declined), audit found {critical} critical issues"

    async def run_weekly_audit(self) -> Dict[str, Any]:
        """Run complete weekly SEO audit with real API data"""
        logger.info("🔍 Starting weekly SEO audit...")
        
        audit_results = {
            "audit_date": datetime.utcnow().isoformat(),
            "critical_issues": [],
            "high_issues": [],
            "medium_issues": [],
            "low_issues": [],
            "auto_fixed": [],
            "recommendations": [],
            "core_web_vitals": None,
            "gsc_summary": None,
            "ranking_summary": None
        }
        
        # 1. Core Web Vitals via PageSpeed Insights API (free)
        try:
            cwv_data = await self._check_core_web_vitals()
            audit_results["core_web_vitals"] = cwv_data
            
            # Flag CWV issues
            if cwv_data.get("lcp", 0) > 2500:
                audit_results["critical_issues"].append({
                    "type": "slow_lcp",
                    "message": f"LCP is {cwv_data['lcp']}ms (should be <2500ms)",
                    "value": cwv_data["lcp"]
                })
            if cwv_data.get("cls", 0) > 0.1:
                audit_results["high_issues"].append({
                    "type": "high_cls",
                    "message": f"CLS is {cwv_data['cls']} (should be <0.1)",
                    "value": cwv_data["cls"]
                })
            if cwv_data.get("fcp", 0) > 1800:
                audit_results["medium_issues"].append({
                    "type": "slow_fcp",
                    "message": f"FCP is {cwv_data['fcp']}ms (should be <1800ms)",
                    "value": cwv_data["fcp"]
                })
        except Exception as e:
            logger.warning(f"PageSpeed check failed: {e}")
        
        # 2. Google Search Console data (if configured)
        try:
            gsc_data = await self._fetch_gsc_data()
            audit_results["gsc_summary"] = gsc_data
        except Exception as e:
            logger.warning(f"GSC fetch skipped: {e}")
        
        # 3. Check keyword rankings from GSC
        try:
            ranking_data = await self._check_keyword_rankings()
            audit_results["ranking_summary"] = ranking_data
        except Exception as e:
            logger.warning(f"Ranking check skipped: {e}")
        
        # 4. Technical SEO checks (HTTP-based)
        try:
            technical_issues = await self._check_technical_seo()
            audit_results["critical_issues"].extend(technical_issues.get("critical", []))
            audit_results["high_issues"].extend(technical_issues.get("high", []))
            audit_results["medium_issues"].extend(technical_issues.get("medium", []))
        except Exception as e:
            logger.warning(f"Technical SEO check failed: {e}")
        
        # 5. Generate recommendations using Claude
        if self.client:
            try:
                recommendations = await self._generate_recommendations(audit_results)
                audit_results["recommendations"] = recommendations
            except Exception as e:
                logger.warning(f"Recommendation generation failed: {e}")
        
        # 6. Save audit to Supabase
        await self._save_audit(audit_results)
        
        # 7. Notify other agents if needed
        try:
            from shared.event_bus import event_bus
            if len(audit_results["critical_issues"]) > 0:
                await event_bus.publish(
                    event_type="seo_critical_issues",
                    target_agent="sama_orchestrator",
                    data={
                        "issue_count": len(audit_results["critical_issues"]),
                        "issues": audit_results["critical_issues"][:5]
                    }
                )
        except Exception:
            pass
        
        logger.info(f"✅ Weekly SEO audit complete. Issues: {len(audit_results['critical_issues'])} critical, {len(audit_results['high_issues'])} high")
        return audit_results
    
    async def track_keyword_rankings(self) -> Dict[str, Any]:
        """Track all keyword rankings and update Supabase"""
        logger.info("📊 Tracking keyword rankings...")
        
        sb = self._get_sb()
        keywords = await self.get_keywords()
        
        results = {
            "total_keywords": len(keywords),
            "improved": [],
            "declined": [],
            "new_top_10": [],
            "lost_top_10": [],
            "updated": 0
        }
        
        # Try to get GSC data for all keywords at once
        gsc_keyword_data = {}
        try:
            gsc_keyword_data = await self._fetch_gsc_keyword_data()
        except Exception as e:
            logger.warning(f"GSC keyword data unavailable: {e}")
        
        for kw in keywords:
            keyword_text = kw["keyword"]
            previous_position = kw.get("current_position")
            
            # Get position from GSC data or None
            gsc_entry = gsc_keyword_data.get(keyword_text.lower(), {})
            current_position = gsc_entry.get("position")
            current_clicks = gsc_entry.get("clicks", kw.get("current_clicks", 0))
            current_impressions = gsc_entry.get("impressions", kw.get("current_impressions", 0))
            current_ctr = gsc_entry.get("ctr", kw.get("current_ctr", 0.0))
            
            # Build position history
            history = kw.get("position_history") or []
            if current_position is not None:
                history.append({
                    "date": datetime.utcnow().isoformat(),
                    "position": current_position,
                    "clicks": current_clicks,
                    "impressions": current_impressions
                })
                history = history[-90:]  # Keep last 90 entries

            # Calculate velocity: compare to position 7 days ago
            position_trend = "stable"
            position_change = 0
            if current_position is not None and len(history) >= 2:
                week_ago = datetime.utcnow() - timedelta(days=7)
                past = [
                    h for h in history[:-1]
                    if datetime.fromisoformat(h["date"].replace("Z", "+00:00").replace("+00:00", "")) < week_ago
                    or True  # take any entry that's older in the list
                ]
                # Use the oldest available within 14 days as baseline
                baseline_entries = [
                    h for h in history[:-1]
                    if (datetime.utcnow() - timedelta(days=14)).isoformat() <= h["date"]
                ]
                if baseline_entries:
                    baseline_pos = baseline_entries[0]["position"]
                    position_change = round(baseline_pos - current_position, 1)
                    if position_change >= 3:
                        position_trend = "improving"
                    elif position_change <= -3:
                        position_trend = "declining"

            # Update keyword in Supabase
            update_data = {
                "current_clicks": current_clicks,
                "current_impressions": current_impressions,
                "current_ctr": current_ctr,
                "position_history": history,
                "position_trend": position_trend,
                "position_change": position_change,
                "last_checked_at": datetime.utcnow().isoformat()
            }
            if current_position is not None:
                update_data["current_position"] = int(current_position)

            try:
                sb.table(KEYWORDS_TABLE).update(update_data).eq("id", kw["id"]).execute()
            except Exception as col_err:
                if "position_change" in str(col_err) or "position_trend" in str(col_err):
                    # Columns not yet added to table — update without them
                    update_data.pop("position_change", None)
                    update_data.pop("position_trend", None)
                    sb.table(KEYWORDS_TABLE).update(update_data).eq("id", kw["id"]).execute()
                else:
                    raise
            results["updated"] += 1
            
            # Detect changes
            if previous_position and current_position:
                if current_position < previous_position:
                    results["improved"].append({
                        "keyword": keyword_text,
                        "from": previous_position,
                        "to": current_position,
                        "change": previous_position - current_position
                    })
                elif current_position > previous_position:
                    results["declined"].append({
                        "keyword": keyword_text,
                        "from": previous_position,
                        "to": current_position,
                        "change": current_position - previous_position
                    })
                if current_position <= 10 and previous_position > 10:
                    results["new_top_10"].append(keyword_text)
                elif current_position > 10 and previous_position <= 10:
                    results["lost_top_10"].append(keyword_text)
        
        # Notify Content Agent
        try:
            from shared.event_bus import event_bus
            if results["new_top_10"]:
                await event_bus.publish(
                    event_type="keywords_entering_top_10",
                    target_agent="sama_content",
                    data={"keywords": results["new_top_10"]}
                )
        except Exception:
            pass
        
        logger.info(f"✅ Keyword tracking complete. Updated: {results['updated']}, Improved: {len(results['improved'])}, Declined: {len(results['declined'])}")
        return results
    
    TARGET_KEYWORDS = [
        # Brand
        {"keyword": "successifier", "intent": "brand", "priority": "high", "target_page": "/"},
        # Core product
        {"keyword": "customer success platform", "intent": "commercial", "priority": "high", "target_page": "/product"},
        {"keyword": "ai customer success", "intent": "commercial", "priority": "high", "target_page": "/product"},
        {"keyword": "customer success software", "intent": "commercial", "priority": "high", "target_page": "/product"},
        {"keyword": "customer success management tool", "intent": "commercial", "priority": "high", "target_page": "/product"},
        {"keyword": "customer health score software", "intent": "commercial", "priority": "high", "target_page": "/product"},
        # Competitor comparison
        {"keyword": "gainsight alternative", "intent": "commercial", "priority": "high", "target_page": "/vs/gainsight"},
        {"keyword": "gainsight vs successifier", "intent": "commercial", "priority": "high", "target_page": "/vs/gainsight"},
        {"keyword": "totango alternative", "intent": "commercial", "priority": "medium", "target_page": "/vs/totango"},
        {"keyword": "churnzero alternative", "intent": "commercial", "priority": "medium", "target_page": "/vs/churnzero"},
        # Informational / blog
        {"keyword": "how to reduce customer churn", "intent": "informational", "priority": "medium", "target_page": "/blog"},
        {"keyword": "customer success metrics", "intent": "informational", "priority": "medium", "target_page": "/blog"},
        {"keyword": "net revenue retention", "intent": "informational", "priority": "medium", "target_page": "/blog"},
        {"keyword": "customer onboarding best practices", "intent": "informational", "priority": "medium", "target_page": "/blog"},
        {"keyword": "churn prediction", "intent": "informational", "priority": "medium", "target_page": "/blog"},
        # Pricing / commercial
        {"keyword": "customer success platform pricing", "intent": "transactional", "priority": "high", "target_page": "/pricing"},
        {"keyword": "best customer success software", "intent": "commercial", "priority": "high", "target_page": "/product"},
    ]

    async def initialize_keywords(self) -> Dict[str, Any]:
        """Seed seo_keywords table with TARGET_KEYWORDS, enriching each with
        live GSC metrics so rows have real data attached on insert."""
        sb = self._get_sb()
        inserted = 0
        skipped = 0
        now = datetime.utcnow().isoformat()

        # Fetch GSC data ONCE and reuse for both seed paths so hardcoded
        # TARGET_KEYWORDS get real metrics instead of zeros.
        try:
            gsc_data = await self._fetch_gsc_keyword_data()
        except Exception as e:
            logger.warning(f"GSC fetch failed during initialize: {e}")
            gsc_data = {}

        # Existing keywords for THIS tenant only — avoids cross-tenant collisions
        existing_result = (
            sb.table(KEYWORDS_TABLE)
            .select("keyword")
            .eq("tenant_id", self.tenant_id)
            .execute()
        )
        existing = {row["keyword"].lower() for row in (existing_result.data or [])}

        for kw in self.TARGET_KEYWORDS:
            kw_lower = kw["keyword"].lower()
            if kw_lower in existing:
                skipped += 1
                continue
            metrics = gsc_data.get(kw_lower, {})
            position = metrics.get("position")
            sb.table(KEYWORDS_TABLE).insert({
                "keyword": kw["keyword"],
                "tenant_id": self.tenant_id,
                "intent": kw["intent"],
                "priority": kw["priority"],
                "target_page": kw["target_page"],
                "current_position": int(position) if position else None,
                "current_clicks": metrics.get("clicks", 0),
                "current_impressions": metrics.get("impressions", 0),
                "current_ctr": metrics.get("ctr", 0.0),
                "position_history": [],
                "last_checked_at": now,
            }).execute()
            inserted += 1

        # Also pull top GSC queries not yet tracked (≥5 impressions to filter noise)
        for query, data in gsc_data.items():
            if query in existing or data.get("impressions", 0) < 5:
                continue
            position = data.get("position")
            try:
                sb.table(KEYWORDS_TABLE).insert({
                    "keyword": query,
                    "tenant_id": self.tenant_id,
                    "intent": "gsc_discovered",
                    "priority": "medium",
                    "target_page": "/",
                    "current_position": int(position) if position else None,
                    "current_clicks": data.get("clicks", 0),
                    "current_impressions": data.get("impressions", 0),
                    "current_ctr": data.get("ctr", 0.0),
                    "position_history": [],
                    "last_checked_at": now,
                }).execute()
                inserted += 1
            except Exception:
                pass

        logger.info(f"✅ initialize_keywords: {inserted} inserted, {skipped} skipped")
        return {"inserted": inserted, "skipped": skipped, "total_target": len(self.TARGET_KEYWORDS)}

    async def discover_keyword_opportunities(self) -> List[Dict[str, Any]]:
        """
        Discover new keyword opportunities using GSC data.
        Scores each query by opportunity size = impressions × (1 − CTR/100).
        High impressions + low CTR = big untapped potential.
        """
        logger.info("🔎 Discovering keyword opportunities...")
        opportunities = []
        try:
            gsc_data = await self._fetch_gsc_keyword_data()
            existing = await self.get_keywords()
            existing_keywords = {k["keyword"].lower() for k in existing}

            for query, data in gsc_data.items():
                if query in existing_keywords:
                    continue
                impressions = data.get("impressions", 0)
                if impressions < 5:
                    continue
                ctr = data.get("ctr", 0)           # already as percentage (e.g. 2.5)
                position = data.get("position")
                clicks = data.get("clicks", 0)
                # Opportunity score: missed clicks
                opportunity_score = round(impressions * (1 - ctr / 100), 1)
                # Category: page-2 keywords are quick wins
                category = "quick_win" if position and 10 < position <= 20 else "untapped"
                opportunities.append({
                    "keyword": query,
                    "impressions": impressions,
                    "clicks": clicks,
                    "position": position,
                    "ctr": ctr,
                    "opportunity_score": opportunity_score,
                    "category": category,
                    "source": "gsc_discovery"
                })

            # Sort by opportunity score descending
            opportunities.sort(key=lambda x: x["opportunity_score"], reverse=True)
        except Exception as e:
            logger.warning(f"Keyword discovery failed: {e}")

        return opportunities[:30]

    async def sync_gsc_keywords(self, min_impressions: int = 1) -> Dict[str, Any]:
        """
        Sync ALL keywords from Google Search Console into seo_keywords table.
        - New keywords get inserted with their GSC data
        - Existing keywords get their metrics updated
        Returns counts of inserted, updated, and total GSC queries.
        """
        logger.info("🔄 Syncing all GSC keywords to seo_keywords table...")
        sb = self._get_sb()

        # Fetch all GSC keyword data (paginated, no limit)
        gsc_data = await self._fetch_gsc_keyword_data()
        if not gsc_data:
            return {"inserted": 0, "updated": 0, "total_gsc": 0, "message": "No GSC data available"}

        # Get existing keywords for THIS tenant from DB
        existing_result = (
            sb.table(KEYWORDS_TABLE)
            .select("id,keyword,current_position")
            .eq("tenant_id", self.tenant_id)
            .execute()
        )
        existing_map = {row["keyword"].lower(): row for row in (existing_result.data or [])}

        inserted = 0
        updated = 0
        now = datetime.utcnow().isoformat()

        for query, data in gsc_data.items():
            impressions = data.get("impressions", 0)
            if impressions < min_impressions:
                continue

            position = data.get("position")
            clicks = data.get("clicks", 0)
            ctr = data.get("ctr", 0.0)

            if query.lower() in existing_map:
                # Update existing keyword with fresh GSC data
                row = existing_map[query.lower()]
                try:
                    sb.table(KEYWORDS_TABLE).update({
                        "current_position": int(position) if position else None,
                        "current_clicks": clicks,
                        "current_impressions": impressions,
                        "current_ctr": ctr,
                        "last_checked_at": now,
                    }).eq("id", row["id"]).execute()
                    updated += 1
                except Exception:
                    pass
            else:
                # Insert new keyword discovered from GSC
                # Determine intent from query
                intent = "gsc_discovered"
                query_lower = query.lower()
                if "successifier" in query_lower or "succesif" in query_lower:
                    intent = "brand"
                elif any(w in query_lower for w in ["price", "pricing", "cost", "buy", "demo", "trial"]):
                    intent = "transactional"
                elif any(w in query_lower for w in ["vs", "alternative", "compare", "best"]):
                    intent = "commercial"
                elif any(w in query_lower for w in ["how to", "what is", "guide", "tips", "why"]):
                    intent = "informational"

                priority = "high" if clicks > 0 or impressions >= 20 else "medium" if impressions >= 5 else "low"

                try:
                    sb.table(KEYWORDS_TABLE).insert({
                        "keyword": query,
                        "tenant_id": self.tenant_id,
                        "intent": intent,
                        "priority": priority,
                        "target_page": data.get("page", "/"),
                        "current_position": int(position) if position else None,
                        "current_clicks": clicks,
                        "current_impressions": impressions,
                        "current_ctr": ctr,
                        "position_history": [],
                        "last_checked_at": now,
                    }).execute()
                    inserted += 1
                except Exception as e:
                    logger.debug(f"Insert failed for keyword '{query}' (tenant={self.tenant_id}): {e}")

        logger.info(f"✅ GSC sync: {inserted} new keywords, {updated} updated, {len(gsc_data)} total GSC queries")
        return {
            "inserted": inserted,
            "updated": updated,
            "total_gsc": len(gsc_data),
            "total_tracked": len(existing_map) + inserted,
        }

    async def get_ctr_opportunities(self) -> List[Dict[str, Any]]:
        """
        Find tracked keywords where CTR is below 2% despite decent position (≤20).
        These are title/meta-description optimization targets — quick wins.
        """
        keywords = await self.get_keywords()
        opportunities = []
        for kw in keywords:
            pos = kw.get("current_position")
            ctr = kw.get("current_ctr", 0)
            impressions = kw.get("current_impressions", 0)
            if pos and pos <= 20 and impressions >= 20 and ctr < 2.0:
                missed_clicks = round(impressions * (0.02 - ctr / 100))
                opportunities.append({
                    "keyword": kw["keyword"],
                    "current_position": pos,
                    "current_ctr": ctr,
                    "impressions": impressions,
                    "current_clicks": kw.get("current_clicks", 0),
                    "missed_clicks_estimate": missed_clicks,
                    "suggestion": "Optimise title tag and meta description to improve CTR",
                    "target_page": kw.get("target_page", "/"),
                })
        # Sort by potential impact
        opportunities.sort(key=lambda x: x["missed_clicks_estimate"], reverse=True)
        return opportunities
    
    # ── Real API integrations ──────────────────────────────────────────
    
    async def _fetch_pagespeed(self, url: str, strategy: str) -> Dict[str, Any]:
        """Fetch PageSpeed Insights data for one URL + strategy"""
        params = {"url": url, "strategy": strategy, "category": "performance"}
        resp = await self.http_client.get(PAGESPEED_API, params=params, timeout=45.0)
        if resp.status_code != 200:
            return {"error": f"API returned {resp.status_code}"}
        data = resp.json()
        audits = data.get("lighthouseResult", {}).get("audits", {})
        perf_score = (
            data.get("lighthouseResult", {})
            .get("categories", {})
            .get("performance", {})
            .get("score", 0)
        )
        # TTFB from server-response-time audit
        ttfb = audits.get("server-response-time", {}).get("numericValue", 0)
        # INP (Interaction to Next Paint) — replaces FID in Core Web Vitals 2024
        inp = audits.get("interactive", {}).get("numericValue", 0)
        return {
            "lcp":               round(audits.get("largest-contentful-paint", {}).get("numericValue", 0), 0),
            "fcp":               round(audits.get("first-contentful-paint", {}).get("numericValue", 0), 0),
            "cls":               round(audits.get("cumulative-layout-shift", {}).get("numericValue", 0), 4),
            "tbt":               round(audits.get("total-blocking-time", {}).get("numericValue", 0), 0),
            "speed_index":       round(audits.get("speed-index", {}).get("numericValue", 0), 0),
            "ttfb":              round(ttfb, 0),
            "tti":               round(inp, 0),
            "performance_score": round(perf_score * 100, 0) if perf_score else 0,
            "strategy":          strategy,
            "url":               url,
        }

    async def _check_core_web_vitals(self) -> Dict[str, Any]:
        """Check Core Web Vitals for mobile (primary) + desktop in parallel"""
        mobile, desktop = await asyncio.gather(
            self._fetch_pagespeed(SITE_URL, "mobile"),
            self._fetch_pagespeed(SITE_URL, "desktop"),
            return_exceptions=True
        )
        if isinstance(mobile, Exception):
            mobile = {"error": str(mobile)}
        if isinstance(desktop, Exception):
            desktop = {"error": str(desktop)}

        # Top-level fields are mobile (Google's ranking signal is mobile-first)
        result = {**mobile}
        result["desktop"] = desktop
        return result
    
    async def _fetch_gsc_data(self) -> Dict[str, Any]:
        """Fetch Google Search Console summary data via real API"""
        if not is_gsc_configured():
            return {"status": "not_configured", "message": "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN"}
        
        token = await get_access_token("gsc")
        if not token:
            return {"status": "auth_failed", "message": "Could not get Google access token"}
        
        # Query last 28 days
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=28)).strftime("%Y-%m-%d")
        
        encoded_site = self.gsc_site_url.replace(':', '%3A').replace('/', '%2F')
        url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"

        resp = await self.http_client.post(url, json={
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": [],
            "rowLimit": 1
        }, headers={"Authorization": f"Bearer {token}"})
        
        if resp.status_code != 200:
            logger.warning(f"GSC API error: {resp.status_code} {resp.text[:200]}")
            return {"status": "error", "message": f"GSC API returned {resp.status_code}"}
        
        data = resp.json()
        rows = data.get("rows", [])
        
        if rows:
            row = rows[0]
            return {
                "status": "ok",
                "total_clicks": row.get("clicks", 0),
                "total_impressions": row.get("impressions", 0),
                "avg_ctr": round(row.get("ctr", 0) * 100, 2),
                "avg_position": round(row.get("position", 0), 1),
                "date_range": f"{start_date} to {end_date}"
            }
        
        return {"status": "ok", "total_clicks": 0, "total_impressions": 0, "avg_ctr": 0.0, "avg_position": 0.0}
    
    async def _fetch_gsc_paginated(
        self,
        dimensions: List[str],
        days: int = 28,
        max_rows: int = 0,
    ) -> List[Dict]:
        """Fetch all rows from GSC using pagination.

        Args:
            dimensions: GSC dimensions e.g. ["query"], ["page"]
            days: Number of days to look back (default 28)
            max_rows: Max rows to fetch. 0 = fetch all available rows.

        Returns:
            List of raw GSC row dicts with keys, clicks, impressions, ctr, position.
        """
        if not is_gsc_configured():
            return []

        token = await get_access_token("gsc")
        if not token:
            return []

        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        encoded_site = self.gsc_site_url.replace(':', '%3A').replace('/', '%2F')
        url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
        headers = {"Authorization": f"Bearer {token}"}

        all_rows: List[Dict] = []
        page_size = 1000  # GSC max per request
        start_row = 0

        while True:
            batch_limit = page_size
            if max_rows > 0:
                remaining = max_rows - len(all_rows)
                if remaining <= 0:
                    break
                batch_limit = min(page_size, remaining)

            resp = await self.http_client.post(url, json={
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": dimensions,
                "rowLimit": batch_limit,
                "startRow": start_row,
            }, headers=headers)

            if resp.status_code != 200:
                logger.warning(f"GSC paginated query failed: {resp.status_code}")
                break

            rows = resp.json().get("rows", [])
            if not rows:
                break

            all_rows.extend(rows)
            start_row += len(rows)

            # If we got fewer rows than requested, there are no more pages
            if len(rows) < batch_limit:
                break

        return all_rows

    async def _fetch_gsc_keyword_data(self, limit: int = 0) -> Dict[str, Dict]:
        """Fetch per-keyword data from Google Search Console.

        Args:
            limit: Max keywords to fetch. 0 = fetch all (paginated).
        """
        rows = await self._fetch_gsc_paginated(["query"], max_rows=limit)

        result = {}
        for row in rows:
            query = row["keys"][0].lower()
            result[query] = {
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": round(row.get("ctr", 0) * 100, 2),
                "position": round(row.get("position", 0), 1)
            }

        logger.info(f"✅ Fetched GSC data for {len(result)} queries")
        return result
    
    async def _check_keyword_rankings(self) -> Dict[str, Any]:
        """Check keyword rankings summary"""
        keywords = await self.get_keywords()
        
        top_3 = sum(1 for k in keywords if k.get("current_position") and k["current_position"] <= 3)
        top_10 = sum(1 for k in keywords if k.get("current_position") and k["current_position"] <= 10)
        page_2 = sum(1 for k in keywords if k.get("current_position") and 11 <= k["current_position"] <= 20)
        tracked = sum(1 for k in keywords if k.get("current_position") is not None)
        
        return {
            "keywords_tracked": len(keywords),
            "with_position_data": tracked,
            "top_3_count": top_3,
            "top_10_count": top_10,
            "page_2_count": page_2
        }
    
    async def fetch_sitemap_pages(self) -> Set[str]:
        """Crawl sitemap.xml to discover all live pages on the site"""
        pages = set()
        try:
            resp = await self.http_client.get(f"{SITE_URL}/sitemap.xml", follow_redirects=True)
            if resp.status_code == 200:
                # Parse URLs from sitemap XML
                urls = re.findall(r'<loc>(.*?)</loc>', resp.text)
                for url in urls:
                    # Normalize to path only
                    path = url.replace("https://www.successifier.com", "").replace("https://successifier.com", "")
                    if not path:
                        path = "/"
                    pages.add(path.rstrip("/") or "/")
                logger.info(f"✅ Sitemap: found {len(pages)} pages")
            else:
                logger.warning(f"Sitemap returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Sitemap fetch failed: {e}")
        return pages

    async def get_known_pages(self) -> Dict[str, Any]:
        """
        Build a complete picture of all known pages:
        - From sitemap.xml (live pages)
        - From seo_keywords target_page (expected pages)
        - From content table url_path (generated content)
        Returns dict with 'live', 'expected', 'content_urls' sets and 'missing' list
        """
        sb = self._get_sb()

        # 1. Live pages from sitemap
        live_pages = await self.fetch_sitemap_pages()

        # 2. Expected pages from keyword targets
        expected_pages = set()
        try:
            kw_result = sb.table(KEYWORDS_TABLE).select("target_page").execute()
            for row in (kw_result.data or []):
                if row.get("target_page"):
                    expected_pages.add(row["target_page"].rstrip("/") or "/")
        except Exception:
            pass

        # 3. Content URLs from content table
        content_urls = set()
        try:
            content_result = sb.table("content").select("url_path, title, status").execute()
            for row in (content_result.data or []):
                if row.get("url_path"):
                    content_urls.add(row["url_path"].rstrip("/") or "/")
        except Exception:
            pass

        # 4. Find missing pages (expected but not live)
        all_expected = expected_pages | content_urls
        missing = [p for p in all_expected if p not in live_pages and p != "/blog"]

        return {
            "live": live_pages,
            "expected": expected_pages,
            "content_urls": content_urls,
            "missing": missing,
            "live_list": sorted(live_pages),
        }

    async def _check_technical_seo(self) -> Dict[str, List[Dict]]:
        """Run HTTP-based technical SEO checks on all known pages"""
        issues = {"critical": [], "high": [], "medium": []}

        # Build dynamic page list from sitemap + DB
        known = await self.get_known_pages()
        live_pages = known["live"]

        pages_to_check = set()
        pages_to_check.update(live_pages)
        pages_to_check.update(known["expected"])
        pages_to_check.update(["/", "/product", "/pricing"])

        checked = 0
        for page in sorted(pages_to_check):
            url = f"{SITE_URL}{page}"
            try:
                import time as _time
                t0 = _time.monotonic()
                resp = await self.http_client.get(url, follow_redirects=True, timeout=15.0)
                response_time_ms = round((_time.monotonic() - t0) * 1000)
                checked += 1

                if resp.status_code == 404:
                    severity = "critical" if (page in known["expected"] or page in known.get("content_urls", set())) else "medium"
                    issues[severity].append({
                        "type": "page_not_found",
                        "url": url,
                        "status_code": 404,
                        "message": f"Expected page {page} returns 404"
                    })
                elif resp.status_code >= 500:
                    issues["critical"].append({
                        "type": "server_error",
                        "url": url,
                        "status_code": resp.status_code
                    })
                elif 300 <= resp.status_code < 400:
                    issues["medium"].append({
                        "type": "redirect",
                        "url": url,
                        "status_code": resp.status_code
                    })

                # Slow TTFB at the page level
                if resp.status_code == 200 and response_time_ms > 800:
                    severity = "high" if response_time_ms > 2000 else "medium"
                    issues[severity].append({
                        "type": "slow_response",
                        "url": url,
                        "response_time_ms": response_time_ms,
                        "message": f"Response time {response_time_ms}ms (target <800ms)"
                    })

                if resp.status_code == 200:
                    html_raw = resp.text
                    soup = BeautifulSoup(html_raw, "lxml")
                    html = html_raw.lower()

                    # Title tag
                    title_tag = soup.find("title")
                    if not title_tag:
                        issues["high"].append({"type": "missing_title", "url": url})
                    else:
                        title_len = len(title_tag.get_text().strip())
                        if title_len > 60:
                            issues["medium"].append({
                                "type": "title_too_long",
                                "url": url,
                                "length": title_len,
                                "message": f"Title is {title_len} chars (target ≤60)"
                            })
                        elif title_len < 30:
                            issues["medium"].append({
                                "type": "title_too_short",
                                "url": url,
                                "length": title_len,
                                "message": f"Title is only {title_len} chars (target ≥30)"
                            })

                    # Meta description
                    meta_desc = soup.find("meta", attrs={"name": "description"})
                    if not meta_desc:
                        issues["high"].append({"type": "missing_meta_description", "url": url})
                    else:
                        desc_len = len((meta_desc.get("content") or "").strip())
                        if desc_len > 160:
                            issues["medium"].append({
                                "type": "meta_description_too_long",
                                "url": url,
                                "length": desc_len,
                                "message": f"Meta description is {desc_len} chars (target ≤160)"
                            })
                        elif desc_len < 50:
                            issues["medium"].append({
                                "type": "meta_description_too_short",
                                "url": url,
                                "length": desc_len,
                                "message": f"Meta description only {desc_len} chars (target ≥50)"
                            })

                    # H1 check
                    h1_tags = soup.find_all("h1")
                    if not h1_tags:
                        issues["medium"].append({"type": "missing_h1", "url": url})
                    elif len(h1_tags) > 1:
                        issues["medium"].append({
                            "type": "multiple_h1",
                            "url": url,
                            "count": len(h1_tags),
                            "message": f"Page has {len(h1_tags)} H1 tags (should have exactly 1)"
                        })

                    # Canonical
                    if 'rel="canonical"' not in html:
                        issues["medium"].append({"type": "missing_canonical", "url": url})

                    # Images without alt text
                    imgs_without_alt = [
                        img.get("src", "")[:80]
                        for img in soup.find_all("img")
                        if not img.get("alt")
                    ]
                    if imgs_without_alt:
                        issues["medium"].append({
                            "type": "images_missing_alt",
                            "url": url,
                            "count": len(imgs_without_alt),
                            "message": f"{len(imgs_without_alt)} images missing alt text"
                        })

            except httpx.ConnectError:
                issues["critical"].append({
                    "type": "connection_failed",
                    "url": url,
                    "message": "Could not connect to site"
                })
            except Exception as e:
                logger.warning(f"Check failed for {url}: {e}")

        logger.info(f"✅ Technical SEO: checked {checked} pages, "
                    f"{len(issues['critical'])} critical, {len(issues['high'])} high, {len(issues['medium'])} medium")
        return issues
    
    async def _generate_recommendations(self, audit_data: Dict[str, Any]) -> List[str]:
        """Generate SEO recommendations using Claude (runs in thread to avoid blocking event loop)"""
        cwv = audit_data.get("core_web_vitals", {})

        prompt = f"""Based on this SEO audit data for successifier.com, provide 5 specific actionable recommendations:

Audit Summary:
- Critical Issues: {len(audit_data['critical_issues'])}
- High Issues: {len(audit_data['high_issues'])}
- Medium Issues: {len(audit_data['medium_issues'])}

Core Web Vitals (mobile):
- LCP: {cwv.get('lcp', 'N/A')}ms (target <2500ms)
- CLS: {cwv.get('cls', 'N/A')} (target <0.1)
- TBT: {cwv.get('tbt', 'N/A')}ms
- Performance Score: {cwv.get('performance_score', 'N/A')}/100

Critical Issues:
{audit_data['critical_issues'][:5]}

High Issues:
{audit_data['high_issues'][:5]}

List exactly 5 actionable recommendations, one per line, starting with a number. Be specific to successifier.com."""

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )

        response = await asyncio.to_thread(_call)
        recommendations = response.content[0].text.strip().split("\n")
        return [r.strip() for r in recommendations if r.strip()]
    
    async def _save_audit(self, audit_data: Dict[str, Any]):
        """Save audit results to Supabase"""
        sb = self._get_sb()
        
        record = {
            "audit_date": audit_data["audit_date"],
            "critical_issues": audit_data["critical_issues"],
            "high_issues": audit_data["high_issues"],
            "medium_issues": audit_data["medium_issues"],
            "low_issues": audit_data["low_issues"],
            "auto_fixed": audit_data["auto_fixed"],
            "recommendations": audit_data["recommendations"],
            "summary": f"Audit: {len(audit_data['critical_issues'])} critical, {len(audit_data['high_issues'])} high issues"
        }
        
        cwv = audit_data.get("core_web_vitals")
        if cwv and not cwv.get("error"):
            record["lcp_score"] = cwv.get("lcp")
            record["cls_score"] = cwv.get("cls")
        
        sb.table(SEO_AUDITS_TABLE).insert(record).execute()
        logger.info("✅ Audit saved to Supabase")

    async def get_all_keywords(self) -> list:
        """Get all tracked keywords from database"""
        try:
            sb = get_supabase()
            result = sb.table(KEYWORDS_TABLE).select("*").execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"Failed to get keywords: {e}")
            return []


# Global SEO agent instance
seo_agent = SEOAgent()
