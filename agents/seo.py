"""
SEO Agent - Technical SEO, Keyword Tracking, and On-Page Optimization
Handles all SEO activities for successifier.com
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import AsyncSessionLocal
from shared.event_bus import event_bus
from .models import Keyword, SEOAudit, BacklinkProfile, CompetitorAnalysis

logger = logging.getLogger(__name__)


class SEOAgent:
    """
    SEO Agent responsible for:
    - Technical SEO audits
    - Keyword rank tracking
    - Backlink monitoring
    - On-page optimization
    - Competitor analysis
    """
    
    # Target keywords from SAMA 2.0 spec
    TARGET_KEYWORDS = [
        {"keyword": "customer success platform", "intent": "commercial", "priority": "P0", "target_page": "/product"},
        {"keyword": "AI customer success software", "intent": "commercial", "priority": "P0", "target_page": "/product"},
        {"keyword": "churn prediction software", "intent": "commercial", "priority": "P0", "target_page": "/product#health-scoring"},
        {"keyword": "customer health score tool", "intent": "commercial", "priority": "P1", "target_page": "/product#health-scoring"},
        {"keyword": "reduce SaaS churn", "intent": "informational", "priority": "P1", "target_page": "/blog/reduce-saas-churn"},
        {"keyword": "customer onboarding software", "intent": "commercial", "priority": "P1", "target_page": "/product#onboarding-portal"},
        {"keyword": "customer success automation", "intent": "commercial", "priority": "P1", "target_page": "/product#automations"},
        {"keyword": "NPS CSAT tool SaaS", "intent": "commercial", "priority": "P2", "target_page": "/product#nps-csat"},
        {"keyword": "customer success platform pricing", "intent": "transactional", "priority": "P1", "target_page": "/pricing"},
        {"keyword": "Gainsight alternative", "intent": "commercial", "priority": "P0", "target_page": "/vs/gainsight"},
        {"keyword": "Totango alternative", "intent": "commercial", "priority": "P0", "target_page": "/vs/totango"},
        {"keyword": "ChurnZero alternative", "intent": "commercial", "priority": "P1", "target_page": "/vs/churnzero"},
        {"keyword": "AI native customer success", "intent": "informational", "priority": "P0", "target_page": "/"},
        {"keyword": "customer success software small team", "intent": "commercial", "priority": "P1", "target_page": "/pricing"},
    ]
    
    COMPETITORS = ["gainsight.com", "totango.com", "churnzero.com"]
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)
    
    async def initialize_keywords(self):
        """Initialize keyword tracking database with target keywords"""
        async with AsyncSessionLocal() as session:
            for kw_data in self.TARGET_KEYWORDS:
                # Check if keyword already exists
                from sqlalchemy import select
                result = await session.execute(
                    select(Keyword).where(Keyword.keyword == kw_data["keyword"])
                )
                existing = result.scalar_one_or_none()
                
                if not existing:
                    keyword = Keyword(
                        keyword=kw_data["keyword"],
                        intent=kw_data["intent"],
                        priority=kw_data["priority"],
                        target_page=kw_data["target_page"],
                        auto_discovered=False
                    )
                    session.add(keyword)
                    logger.info(f"Added keyword: {kw_data['keyword']}")
            
            await session.commit()
            logger.info(f"✅ Initialized {len(self.TARGET_KEYWORDS)} target keywords")
    
    async def run_weekly_audit(self) -> Dict[str, Any]:
        """
        Run complete weekly SEO audit
        
        Returns:
            Audit results with issues and recommendations
        """
        logger.info("🔍 Starting weekly SEO audit...")
        
        audit_results = {
            "audit_date": datetime.utcnow().isoformat(),
            "critical_issues": [],
            "high_issues": [],
            "medium_issues": [],
            "low_issues": [],
            "auto_fixed": [],
            "recommendations": []
        }
        
        # 1. Fetch Google Search Console data
        try:
            gsc_data = await self._fetch_gsc_data()
            audit_results["gsc_summary"] = gsc_data
        except Exception as e:
            logger.error(f"GSC fetch failed: {e}")
            audit_results["critical_issues"].append({
                "type": "gsc_connection_failed",
                "message": str(e)
            })
        
        # 2. Check keyword rankings
        try:
            ranking_data = await self._check_keyword_rankings()
            audit_results["ranking_summary"] = ranking_data
        except Exception as e:
            logger.error(f"Ranking check failed: {e}")
        
        # 3. Technical SEO checks (would use Screaming Frog CLI in production)
        technical_issues = await self._check_technical_seo()
        audit_results["critical_issues"].extend(technical_issues.get("critical", []))
        audit_results["high_issues"].extend(technical_issues.get("high", []))
        audit_results["medium_issues"].extend(technical_issues.get("medium", []))
        
        # 4. Core Web Vitals check
        try:
            cwv_data = await self._check_core_web_vitals()
            audit_results["core_web_vitals"] = cwv_data
        except Exception as e:
            logger.error(f"Core Web Vitals check failed: {e}")
        
        # 5. Generate recommendations using Claude
        recommendations = await self._generate_recommendations(audit_results)
        audit_results["recommendations"] = recommendations
        
        # 6. Save audit to database
        await self._save_audit(audit_results)
        
        # 7. Notify other agents if needed
        if len(audit_results["critical_issues"]) > 0:
            await event_bus.publish(
                event_type="seo_critical_issues",
                target_agent="sama_orchestrator",
                data={
                    "issue_count": len(audit_results["critical_issues"]),
                    "issues": audit_results["critical_issues"][:5]  # Top 5
                }
            )
        
        logger.info(f"✅ Weekly SEO audit complete. Issues: {len(audit_results['critical_issues'])} critical, {len(audit_results['high_issues'])} high")
        
        return audit_results
    
    async def track_keyword_rankings(self) -> Dict[str, Any]:
        """
        Track all keyword rankings and update database
        
        Returns:
            Ranking summary with changes
        """
        logger.info("📊 Tracking keyword rankings...")
        
        results = {
            "total_keywords": 0,
            "improved": [],
            "declined": [],
            "new_top_10": [],
            "lost_top_10": []
        }
        
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            result = await session.execute(select(Keyword))
            keywords = result.scalars().all()
            results["total_keywords"] = len(keywords)
            
            for keyword in keywords:
                # Fetch current ranking (would use Semrush API in production)
                current_position = await self._get_keyword_position(keyword.keyword)
                previous_position = keyword.current_position
                
                # Update keyword
                keyword.current_position = current_position
                keyword.last_checked_at = datetime.utcnow()
                
                # Track history
                if keyword.position_history is None:
                    keyword.position_history = []
                
                keyword.position_history.append({
                    "date": datetime.utcnow().isoformat(),
                    "position": current_position,
                    "clicks": keyword.current_clicks,
                    "impressions": keyword.current_impressions
                })
                
                # Keep only last 90 days
                keyword.position_history = keyword.position_history[-90:]
                
                # Detect changes
                if previous_position and current_position:
                    if current_position < previous_position:
                        results["improved"].append({
                            "keyword": keyword.keyword,
                            "from": previous_position,
                            "to": current_position,
                            "change": previous_position - current_position
                        })
                    elif current_position > previous_position:
                        results["declined"].append({
                            "keyword": keyword.keyword,
                            "from": previous_position,
                            "to": current_position,
                            "change": current_position - previous_position
                        })
                    
                    # Track top 10 changes
                    if current_position <= 10 and previous_position > 10:
                        results["new_top_10"].append(keyword.keyword)
                    elif current_position > 10 and previous_position <= 10:
                        results["lost_top_10"].append(keyword.keyword)
            
            await session.commit()
        
        # Notify Content Agent of keyword opportunities
        if results["new_top_10"]:
            await event_bus.publish(
                event_type="keywords_entering_top_10",
                target_agent="sama_content",
                data={"keywords": results["new_top_10"]}
            )
        
        logger.info(f"✅ Keyword tracking complete. Improved: {len(results['improved'])}, Declined: {len(results['declined'])}")
        
        return results
    
    async def discover_keyword_opportunities(self) -> List[Dict[str, Any]]:
        """
        Discover new keyword opportunities using competitor analysis
        
        Returns:
            List of new keyword opportunities
        """
        logger.info("🔎 Discovering keyword opportunities...")
        
        opportunities = []
        
        # This would use Semrush/Ahrefs API in production
        # For now, return placeholder
        
        return opportunities
    
    async def _fetch_gsc_data(self) -> Dict[str, Any]:
        """Fetch Google Search Console data (placeholder)"""
        # Would use Google Search Console API in production
        return {
            "total_clicks": 0,
            "total_impressions": 0,
            "avg_ctr": 0.0,
            "avg_position": 0.0
        }
    
    async def _check_keyword_rankings(self) -> Dict[str, Any]:
        """Check keyword rankings (placeholder)"""
        # Would use Semrush API in production
        return {
            "keywords_tracked": len(self.TARGET_KEYWORDS),
            "top_10_count": 0,
            "page_1_count": 0
        }
    
    async def _get_keyword_position(self, keyword: str) -> Optional[int]:
        """Get current position for keyword (placeholder)"""
        # Would use Semrush API in production
        return None
    
    async def _check_technical_seo(self) -> Dict[str, List[Dict]]:
        """Run technical SEO checks (placeholder)"""
        # Would use Screaming Frog CLI in production
        return {
            "critical": [],
            "high": [],
            "medium": []
        }
    
    async def _check_core_web_vitals(self) -> Dict[str, float]:
        """Check Core Web Vitals (placeholder)"""
        # Would use PageSpeed Insights API in production
        return {
            "lcp": 0.0,
            "inp": 0.0,
            "cls": 0.0
        }
    
    async def _generate_recommendations(self, audit_data: Dict[str, Any]) -> List[str]:
        """Generate SEO recommendations using Claude"""
        
        prompt = f"""Based on this SEO audit data for successifier.com, provide 5 actionable recommendations:

Audit Summary:
- Critical Issues: {len(audit_data['critical_issues'])}
- High Issues: {len(audit_data['high_issues'])}
- Medium Issues: {len(audit_data['medium_issues'])}

Critical Issues:
{audit_data['critical_issues'][:3]}

Provide specific, actionable recommendations prioritized by impact."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Parse recommendations
        recommendations = response.content[0].text.strip().split("\n")
        return [r.strip() for r in recommendations if r.strip()]
    
    async def _save_audit(self, audit_data: Dict[str, Any]):
        """Save audit results to database"""
        async with AsyncSessionLocal() as session:
            audit = SEOAudit(
                audit_date=datetime.utcnow(),
                critical_issues=audit_data["critical_issues"],
                high_issues=audit_data["high_issues"],
                medium_issues=audit_data["medium_issues"],
                low_issues=audit_data["low_issues"],
                auto_fixed=audit_data["auto_fixed"],
                recommendations=audit_data["recommendations"],
                summary=f"Audit completed with {len(audit_data['critical_issues'])} critical issues"
            )
            session.add(audit)
            await session.commit()
            logger.info("✅ Audit saved to database")


# Global SEO agent instance
seo_agent = SEOAgent()
