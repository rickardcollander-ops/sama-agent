"""
Weekly Master Report Generation
Comprehensive weekly performance report across all agents
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import logging

from shared.config import settings
from shared.database import get_supabase
from agents.seo import seo_agent
from agents.ads import ads_agent
from agents.social import social_agent
from agents.reviews import review_agent

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate comprehensive weekly reports"""
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-sonnet-4-20250514"
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def generate_weekly_master_report(self) -> Dict[str, Any]:
        """
        Generate comprehensive weekly report
        Runs every Monday at 07:00 CET (06:00 UTC)
        """
        try:
            # Collect data from all agents
            report_data = await self._collect_weekly_data()
            
            # Generate AI summary
            summary = await self._generate_ai_summary(report_data)
            
            # Calculate KPI trends
            trends = await self._calculate_kpi_trends(report_data)
            
            # Identify wins and issues
            highlights = await self._identify_highlights(report_data)
            
            # Compile final report
            report = {
                "report_id": f"weekly_{datetime.utcnow().strftime('%Y%m%d')}",
                "period_start": (datetime.utcnow() - timedelta(days=7)).isoformat(),
                "period_end": datetime.utcnow().isoformat(),
                "generated_at": datetime.utcnow().isoformat(),
                "summary": summary,
                "kpi_trends": trends,
                "highlights": highlights,
                "agent_reports": report_data,
                "recommendations": await self._generate_recommendations(report_data)
            }
            
            # Save to database
            sb = self._get_sb()
            sb.table("weekly_reports").insert(report).execute()
            
            logger.info(f"Weekly report generated: {report['report_id']}")
            
            return {
                "success": True,
                "report": report
            }
            
        except Exception as e:
            logger.error(f"Failed to generate weekly report: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _collect_weekly_data(self) -> Dict[str, Any]:
        """Collect data from all agents for the past week"""
        data = {}
        
        # SEO data
        try:
            keywords = await seo_agent.get_all_keywords()
            data["seo"] = {
                "total_keywords": len(keywords.get("keywords", [])),
                "avg_position": sum(k.get("current_position", 0) for k in keywords.get("keywords", [])) / len(keywords.get("keywords", [])) if keywords.get("keywords") else 0,
                "total_clicks": sum(k.get("current_clicks", 0) for k in keywords.get("keywords", [])),
                "total_impressions": sum(k.get("current_impressions", 0) for k in keywords.get("keywords", []))
            }
        except Exception as e:
            logger.error(f"Failed to collect SEO data: {e}")
            data["seo"] = {"error": str(e)}
        
        # Ads data
        try:
            campaigns = await ads_agent.get_all_campaigns()
            data["ads"] = {
                "total_campaigns": len(campaigns.get("campaigns", [])),
                "total_spend": sum(c.get("cost", 0) for c in campaigns.get("campaigns", [])),
                "total_conversions": sum(c.get("conversions", 0) for c in campaigns.get("campaigns", [])),
                "avg_cpc": sum(c.get("avg_cpc", 0) for c in campaigns.get("campaigns", [])) / len(campaigns.get("campaigns", [])) if campaigns.get("campaigns") else 0
            }
        except Exception as e:
            logger.error(f"Failed to collect Ads data: {e}")
            data["ads"] = {"error": str(e)}
        
        # Social data
        try:
            sb = self._get_sb()
            week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
            
            posts_result = sb.table("social_posts")\
                .select("*")\
                .gte("created_at", week_ago)\
                .execute()
            
            posts = posts_result.data if posts_result.data else []
            
            data["social"] = {
                "posts_published": len([p for p in posts if p.get("status") == "published"]),
                "total_engagement": sum(p.get("engagement", 0) for p in posts),
                "avg_engagement_rate": sum(p.get("engagement_rate", 0) for p in posts) / len(posts) if posts else 0
            }
        except Exception as e:
            logger.error(f"Failed to collect Social data: {e}")
            data["social"] = {"error": str(e)}
        
        # Reviews data
        try:
            sb = self._get_sb()
            week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
            
            reviews_result = sb.table("reviews")\
                .select("*")\
                .gte("scraped_at", week_ago)\
                .execute()
            
            reviews = reviews_result.data if reviews_result.data else []
            
            data["reviews"] = {
                "new_reviews": len(reviews),
                "avg_rating": sum(r.get("rating", 0) for r in reviews) / len(reviews) if reviews else 0,
                "positive_reviews": len([r for r in reviews if r.get("rating", 0) >= 4]),
                "negative_reviews": len([r for r in reviews if r.get("rating", 0) <= 2])
            }
        except Exception as e:
            logger.error(f"Failed to collect Reviews data: {e}")
            data["reviews"] = {"error": str(e)}
        
        return data
    
    async def _generate_ai_summary(self, data: Dict[str, Any]) -> str:
        """Generate AI-powered executive summary"""
        if not self.client:
            return "AI summary not available (Anthropic API key not configured)"
        
        try:
            prompt = f"""Generate a concise executive summary of this week's marketing performance:

SEO: {data.get('seo')}
Ads: {data.get('ads')}
Social: {data.get('social')}
Reviews: {data.get('reviews')}

Provide:
1. Overall performance (2-3 sentences)
2. Key wins (bullet points)
3. Areas of concern (bullet points)
4. Week-over-week trends

Keep it concise and actionable."""
            
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            return response.content[0].text
            
        except Exception as e:
            logger.error(f"Failed to generate AI summary: {e}")
            return f"AI summary generation failed: {str(e)}"
    
    async def _calculate_kpi_trends(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate week-over-week KPI trends"""
        sb = self._get_sb()
        
        # Get previous week's report
        two_weeks_ago = (datetime.utcnow() - timedelta(days=14)).isoformat()
        one_week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        
        prev_report = sb.table("weekly_reports")\
            .select("*")\
            .gte("period_start", two_weeks_ago)\
            .lt("period_start", one_week_ago)\
            .order("period_start", desc=True)\
            .limit(1)\
            .execute()
        
        if not prev_report.data:
            return {"message": "No previous week data for comparison"}
        
        prev_data = prev_report.data[0].get("agent_reports", {})
        
        trends = {}
        
        # SEO trends
        if "seo" in data and "seo" in prev_data:
            trends["seo"] = {
                "position_change": data["seo"].get("avg_position", 0) - prev_data["seo"].get("avg_position", 0),
                "clicks_change_pct": self._calculate_change_pct(
                    data["seo"].get("total_clicks", 0),
                    prev_data["seo"].get("total_clicks", 1)
                )
            }
        
        # Ads trends
        if "ads" in data and "ads" in prev_data:
            trends["ads"] = {
                "spend_change_pct": self._calculate_change_pct(
                    data["ads"].get("total_spend", 0),
                    prev_data["ads"].get("total_spend", 1)
                ),
                "conversions_change_pct": self._calculate_change_pct(
                    data["ads"].get("total_conversions", 0),
                    prev_data["ads"].get("total_conversions", 1)
                )
            }
        
        return trends
    
    def _calculate_change_pct(self, current: float, previous: float) -> float:
        """Calculate percentage change"""
        if previous == 0:
            return 0.0
        return ((current - previous) / previous) * 100
    
    async def _identify_highlights(self, data: Dict[str, Any]) -> Dict[str, List[str]]:
        """Identify key wins and issues"""
        wins = []
        issues = []
        
        # SEO highlights
        if "seo" in data:
            if data["seo"].get("avg_position", 0) < 3.0:
                wins.append(f"Excellent SEO performance: Avg position {data['seo']['avg_position']:.1f}")
            if data["seo"].get("total_clicks", 0) > 100:
                wins.append(f"Strong organic traffic: {data['seo']['total_clicks']} clicks this week")
        
        # Ads highlights
        if "ads" in data:
            if data["ads"].get("total_conversions", 0) > 20:
                wins.append(f"High conversion volume: {data['ads']['total_conversions']} conversions")
            if data["ads"].get("avg_cpc", 999) < 5.0:
                wins.append(f"Efficient CPC: ${data['ads']['avg_cpc']:.2f} average")
        
        # Reviews highlights
        if "reviews" in data:
            if data["reviews"].get("avg_rating", 0) >= 4.5:
                wins.append(f"Excellent review rating: {data['reviews']['avg_rating']:.1f}/5.0")
            if data["reviews"].get("negative_reviews", 0) > 0:
                issues.append(f"{data['reviews']['negative_reviews']} negative reviews need attention")
        
        return {
            "wins": wins,
            "issues": issues
        }
    
    async def _generate_recommendations(self, data: Dict[str, Any]) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        # SEO recommendations
        if "seo" in data:
            if data["seo"].get("avg_position", 0) > 5.0:
                recommendations.append("Focus on improving keyword rankings - consider content refresh")
        
        # Ads recommendations
        if "ads" in data:
            if data["ads"].get("avg_cpc", 0) > 10.0:
                recommendations.append("CPC is high - review keyword quality scores and ad relevance")
        
        # Social recommendations
        if "social" in data:
            if data["social"].get("posts_published", 0) < 5:
                recommendations.append("Increase social posting frequency to 1-2 posts per day")
        
        return recommendations


# Global instance
report_generator = ReportGenerator()
