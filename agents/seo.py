"""
SEO Agent - Technical SEO, Keyword Tracking, and On-Page Optimization
Handles all SEO activities for successifier.com
Uses Supabase for persistence and real Google APIs where configured.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from shared.google_auth import get_access_token, is_gsc_configured
from .models import KEYWORDS_TABLE, SEO_AUDITS_TABLE, BACKLINK_PROFILES_TABLE, COMPETITOR_ANALYSES_TABLE

logger = logging.getLogger(__name__)

# PageSpeed Insights API (free, no key required for basic usage)
PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Google Search Console API
GSC_API = "https://searchconsole.googleapis.com/webmasters/v3"
# GSC uses sc-domain: format for domain properties
GSC_SITE_URL = "sc-domain:successifier.com"
# HTTP URL for PageSpeed and technical checks
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
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-sonnet-4-5-20250929"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def get_keywords(self) -> List[Dict[str, Any]]:
        """Get all tracked keywords from Supabase"""
        sb = self._get_sb()
        result = sb.table(KEYWORDS_TABLE).select("*").execute()
        return result.data or []
    
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
            
            # Update keyword in Supabase
            update_data = {
                "current_clicks": current_clicks,
                "current_impressions": current_impressions,
                "current_ctr": current_ctr,
                "position_history": history,
                "last_checked_at": datetime.utcnow().isoformat()
            }
            if current_position is not None:
                update_data["current_position"] = int(current_position)
            
            sb.table(KEYWORDS_TABLE).update(update_data).eq("id", kw["id"]).execute()
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
        """Seed seo_keywords table with TARGET_KEYWORDS, then enrich from GSC if available"""
        sb = self._get_sb()
        inserted = 0
        skipped = 0

        # Fetch existing keywords to avoid duplicates
        existing_result = sb.table(KEYWORDS_TABLE).select("keyword").execute()
        existing = {row["keyword"].lower() for row in (existing_result.data or [])}

        for kw in self.TARGET_KEYWORDS:
            if kw["keyword"].lower() in existing:
                skipped += 1
                continue
            sb.table(KEYWORDS_TABLE).insert({
                "keyword": kw["keyword"],
                "intent": kw["intent"],
                "priority": kw["priority"],
                "target_page": kw["target_page"],
                "current_position": None,
                "current_clicks": 0,
                "current_impressions": 0,
                "current_ctr": 0.0,
                "position_history": []
            }).execute()
            inserted += 1

        # Also pull top GSC queries not yet tracked
        try:
            gsc_data = await self._fetch_gsc_keyword_data(limit=50)
            for query, data in gsc_data.items():
                if query in existing or data.get("impressions", 0) < 5:
                    continue
                try:
                    sb.table(KEYWORDS_TABLE).insert({
                        "keyword": query,
                        "intent": "gsc_discovered",
                        "priority": "medium",
                        "target_page": "/",
                        "current_position": int(data.get("position", 0)) or None,
                        "current_clicks": data.get("clicks", 0),
                        "current_impressions": data.get("impressions", 0),
                        "current_ctr": data.get("ctr", 0.0),
                        "position_history": []
                    }).execute()
                    inserted += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"GSC seed failed during initialize: {e}")

        logger.info(f"✅ initialize_keywords: {inserted} inserted, {skipped} skipped")
        return {"inserted": inserted, "skipped": skipped, "total_target": len(self.TARGET_KEYWORDS)}

    async def discover_keyword_opportunities(self) -> List[Dict[str, Any]]:
        """Discover new keyword opportunities using GSC data"""
        logger.info("🔎 Discovering keyword opportunities...")
        
        opportunities = []
        
        try:
            gsc_data = await self._fetch_gsc_keyword_data(limit=100)
            existing = await self.get_keywords()
            existing_keywords = {k["keyword"].lower() for k in existing}
            
            for query, data in gsc_data.items():
                if query not in existing_keywords and data.get("impressions", 0) > 10:
                    opportunities.append({
                        "keyword": query,
                        "impressions": data["impressions"],
                        "clicks": data["clicks"],
                        "position": data.get("position"),
                        "ctr": data.get("ctr", 0),
                        "source": "gsc_discovery"
                    })
            
            # Sort by impressions
            opportunities.sort(key=lambda x: x["impressions"], reverse=True)
        except Exception as e:
            logger.warning(f"Keyword discovery failed: {e}")
        
        return opportunities[:20]
    
    # ── Real API integrations ──────────────────────────────────────────
    
    async def _check_core_web_vitals(self) -> Dict[str, Any]:
        """Check Core Web Vitals via PageSpeed Insights API (free, no key needed)"""
        params = {
            "url": SITE_URL,
            "strategy": "mobile",
            "category": "performance"
        }
        
        resp = await self.http_client.get(PAGESPEED_API, params=params)
        
        if resp.status_code != 200:
            logger.warning(f"PageSpeed API returned {resp.status_code}")
            return {"error": f"API returned {resp.status_code}"}
        
        data = resp.json()
        
        # Extract metrics from Lighthouse
        audits = data.get("lighthouseResult", {}).get("audits", {})
        
        lcp = audits.get("largest-contentful-paint", {}).get("numericValue", 0)
        fcp = audits.get("first-contentful-paint", {}).get("numericValue", 0)
        cls = audits.get("cumulative-layout-shift", {}).get("numericValue", 0)
        tbt = audits.get("total-blocking-time", {}).get("numericValue", 0)
        si = audits.get("speed-index", {}).get("numericValue", 0)
        
        # Overall performance score
        perf_score = data.get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score", 0)
        
        return {
            "lcp": round(lcp, 0),
            "fcp": round(fcp, 0),
            "cls": round(cls, 4),
            "tbt": round(tbt, 0),
            "speed_index": round(si, 0),
            "performance_score": round(perf_score * 100, 0) if perf_score else 0,
            "strategy": "mobile",
            "url": SITE_URL
        }
    
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
        
        encoded_site = GSC_SITE_URL.replace(':', '%3A').replace('/', '%2F')
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
    
    async def _fetch_gsc_keyword_data(self, limit: int = 50) -> Dict[str, Dict]:
        """Fetch per-keyword data from Google Search Console"""
        if not is_gsc_configured():
            return {}
        
        token = await get_access_token("gsc")
        if not token:
            return {}
        
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=28)).strftime("%Y-%m-%d")
        
        encoded_site = GSC_SITE_URL.replace(':', '%3A').replace('/', '%2F')
        url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
        
        resp = await self.http_client.post(url, json={
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query"],
            "rowLimit": limit
        }, headers={"Authorization": f"Bearer {token}"})
        
        if resp.status_code != 200:
            logger.warning(f"GSC keyword query failed: {resp.status_code}")
            return {}
        
        data = resp.json()
        result = {}
        
        for row in data.get("rows", []):
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
    
    async def _check_technical_seo(self) -> Dict[str, List[Dict]]:
        """Run HTTP-based technical SEO checks on successifier.com"""
        issues = {"critical": [], "high": [], "medium": []}
        
        pages_to_check = [
            "/", "/product", "/pricing", "/blog",
            "/vs/gainsight", "/vs/totango", "/vs/churnzero"
        ]
        
        for page in pages_to_check:
            url = f"{SITE_URL}{page}"
            try:
                resp = await self.http_client.get(url, follow_redirects=True)
                
                if resp.status_code == 404:
                    issues["critical"].append({
                        "type": "page_not_found",
                        "url": url,
                        "status_code": 404
                    })
                elif resp.status_code >= 500:
                    issues["critical"].append({
                        "type": "server_error",
                        "url": url,
                        "status_code": resp.status_code
                    })
                elif resp.status_code >= 300 and resp.status_code < 400:
                    issues["medium"].append({
                        "type": "redirect",
                        "url": url,
                        "status_code": resp.status_code
                    })
                
                # Check response time
                # httpx doesn't expose elapsed easily, so we skip timing for now
                
                # Check for basic SEO elements in HTML
                if resp.status_code == 200:
                    html = resp.text.lower()
                    if "<title>" not in html or "</title>" not in html:
                        issues["high"].append({
                            "type": "missing_title",
                            "url": url
                        })
                    if 'meta name="description"' not in html:
                        issues["high"].append({
                            "type": "missing_meta_description",
                            "url": url
                        })
                    if "<h1" not in html:
                        issues["medium"].append({
                            "type": "missing_h1",
                            "url": url
                        })
                    if 'rel="canonical"' not in html:
                        issues["medium"].append({
                            "type": "missing_canonical",
                            "url": url
                        })
                        
            except httpx.ConnectError:
                issues["critical"].append({
                    "type": "connection_failed",
                    "url": url,
                    "message": "Could not connect to site"
                })
            except Exception as e:
                logger.warning(f"Check failed for {url}: {e}")
        
        return issues
    
    async def _generate_recommendations(self, audit_data: Dict[str, Any]) -> List[str]:
        """Generate SEO recommendations using Claude"""
        cwv = audit_data.get("core_web_vitals", {})
        
        prompt = f"""Based on this SEO audit data for successifier.com, provide 5 actionable recommendations:

Audit Summary:
- Critical Issues: {len(audit_data['critical_issues'])}
- High Issues: {len(audit_data['high_issues'])}
- Medium Issues: {len(audit_data['medium_issues'])}

Core Web Vitals:
- LCP: {cwv.get('lcp', 'N/A')}ms
- CLS: {cwv.get('cls', 'N/A')}
- Performance Score: {cwv.get('performance_score', 'N/A')}/100

Critical Issues:
{audit_data['critical_issues'][:5]}

High Issues:
{audit_data['high_issues'][:5]}

Provide specific, actionable recommendations prioritized by impact. Be concise."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        
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


# Global SEO agent instance
seo_agent = SEOAgent()
