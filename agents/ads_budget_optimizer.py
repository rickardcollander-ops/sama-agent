"""
Dynamic Budget Optimization for Ads Agent
Automatically reallocates budget from low-ROAS to high-ROAS campaigns
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging

from shared.database import get_supabase
from shared.alerts import alert_system, Alert, AlertType, AlertSeverity

logger = logging.getLogger(__name__)


class BudgetOptimizer:
    """Optimize budget allocation across campaigns"""
    
    # Thresholds
    MIN_ROAS = 2.0  # Minimum acceptable ROAS
    BUDGET_CHANGE_THRESHOLD = 0.30  # 30% - requires approval
    MIN_CAMPAIGN_BUDGET = 10.0  # Minimum daily budget
    
    def __init__(self):
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def analyze_campaign_performance(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Analyze campaign performance over the past N days
        
        Args:
            days: Number of days to analyze
        
        Returns:
            List of campaigns with performance metrics
        """
        try:
            sb = self._get_sb()
            
            # Get campaign performance data
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            result = sb.table("campaign_performance")\
                .select("*")\
                .gte("date", cutoff)\
                .execute()
            
            performance_data = result.data if result.data else []
            
            # Aggregate by campaign
            campaigns = {}
            for record in performance_data:
                campaign_id = record.get("campaign_id")
                
                if campaign_id not in campaigns:
                    campaigns[campaign_id] = {
                        "campaign_id": campaign_id,
                        "campaign_name": record.get("campaign_name"),
                        "total_spend": 0,
                        "total_conversions": 0,
                        "total_revenue": 0,
                        "current_budget": record.get("daily_budget", 0)
                    }
                
                campaigns[campaign_id]["total_spend"] += record.get("cost", 0)
                campaigns[campaign_id]["total_conversions"] += record.get("conversions", 0)
                campaigns[campaign_id]["total_revenue"] += record.get("conversion_value", 0)
            
            # Calculate ROAS for each campaign
            campaign_list = []
            for campaign in campaigns.values():
                spend = campaign["total_spend"]
                revenue = campaign["total_revenue"]
                
                roas = (revenue / spend) if spend > 0 else 0
                cpa = (spend / campaign["total_conversions"]) if campaign["total_conversions"] > 0 else 0
                
                campaign_list.append({
                    **campaign,
                    "roas": roas,
                    "cpa": cpa,
                    "performance_score": self._calculate_performance_score(roas, cpa)
                })
            
            # Sort by performance score
            campaign_list.sort(key=lambda x: x["performance_score"], reverse=True)
            
            return campaign_list
            
        except Exception as e:
            logger.error(f"Failed to analyze campaign performance: {e}")
            return []
    
    def _calculate_performance_score(self, roas: float, cpa: float) -> float:
        """Calculate overall performance score"""
        # Weight ROAS more heavily (70%) than CPA (30%)
        roas_score = min(roas / 5.0, 1.0) * 0.7  # Normalize to 0-1, max at 5.0 ROAS
        cpa_score = max(1.0 - (cpa / 100.0), 0) * 0.3  # Lower CPA is better
        
        return roas_score + cpa_score
    
    async def optimize_budgets(self, total_budget: float) -> Dict[str, Any]:
        """
        Optimize budget allocation across campaigns
        
        Args:
            total_budget: Total daily budget to allocate
        
        Returns:
            Optimization results with recommended budget changes
        """
        try:
            campaigns = await self.analyze_campaign_performance(days=7)
            
            if not campaigns:
                return {
                    "success": False,
                    "error": "No campaign data available"
                }
            
            # Separate high and low performers
            high_performers = [c for c in campaigns if c["roas"] >= self.MIN_ROAS]
            low_performers = [c for c in campaigns if c["roas"] < self.MIN_ROAS]
            
            # Calculate new budget allocation
            recommendations = []
            requires_approval = []
            
            # Reduce budget for low performers
            freed_budget = 0
            for campaign in low_performers:
                current_budget = campaign["current_budget"]
                
                # Reduce by 20-50% depending on how bad ROAS is
                if campaign["roas"] < 1.0:
                    reduction_pct = 0.50  # 50% reduction for ROAS < 1.0
                else:
                    reduction_pct = 0.20  # 20% reduction for ROAS 1.0-2.0
                
                new_budget = max(current_budget * (1 - reduction_pct), self.MIN_CAMPAIGN_BUDGET)
                budget_change = new_budget - current_budget
                change_pct = abs(budget_change / current_budget)
                
                recommendation = {
                    "campaign_id": campaign["campaign_id"],
                    "campaign_name": campaign["campaign_name"],
                    "current_budget": current_budget,
                    "recommended_budget": new_budget,
                    "change_amount": budget_change,
                    "change_percentage": change_pct,
                    "reason": f"Low ROAS ({campaign['roas']:.2f})",
                    "roas": campaign["roas"]
                }
                
                if change_pct > self.BUDGET_CHANGE_THRESHOLD:
                    requires_approval.append(recommendation)
                else:
                    recommendations.append(recommendation)
                    freed_budget += abs(budget_change)
            
            # Increase budget for high performers
            if freed_budget > 0 and high_performers:
                # Distribute freed budget proportionally to performance
                total_performance = sum(c["performance_score"] for c in high_performers)
                
                for campaign in high_performers:
                    current_budget = campaign["current_budget"]
                    
                    # Allocate freed budget proportionally
                    performance_share = campaign["performance_score"] / total_performance
                    budget_increase = freed_budget * performance_share
                    
                    new_budget = current_budget + budget_increase
                    change_pct = budget_increase / current_budget
                    
                    recommendation = {
                        "campaign_id": campaign["campaign_id"],
                        "campaign_name": campaign["campaign_name"],
                        "current_budget": current_budget,
                        "recommended_budget": new_budget,
                        "change_amount": budget_increase,
                        "change_percentage": change_pct,
                        "reason": f"High ROAS ({campaign['roas']:.2f})",
                        "roas": campaign["roas"]
                    }
                    
                    if change_pct > self.BUDGET_CHANGE_THRESHOLD:
                        requires_approval.append(recommendation)
                    else:
                        recommendations.append(recommendation)
            
            # Create alerts for changes requiring approval
            for rec in requires_approval:
                await alert_system.send_alert(Alert(
                    type=AlertType.BUDGET_CHANGE,
                    severity=AlertSeverity.CRITICAL,
                    title=f"Budget Change Approval Required: {rec['campaign_name']}",
                    message=f"Recommended budget change of {rec['change_percentage']*100:.1f}% (${rec['current_budget']:.2f} → ${rec['recommended_budget']:.2f})",
                    data=rec,
                    agent="ads_agent",
                    requires_approval=True
                ))
            
            # Save optimization results
            sb = self._get_sb()
            sb.table("budget_optimizations").insert({
                "total_budget": total_budget,
                "recommendations": recommendations,
                "requires_approval": requires_approval,
                "high_performers": len(high_performers),
                "low_performers": len(low_performers),
                "freed_budget": freed_budget,
                "optimized_at": datetime.utcnow().isoformat()
            }).execute()
            
            return {
                "success": True,
                "total_recommendations": len(recommendations) + len(requires_approval),
                "auto_apply": recommendations,
                "requires_approval": requires_approval,
                "freed_budget": freed_budget,
                "summary": {
                    "high_performers": len(high_performers),
                    "low_performers": len(low_performers),
                    "avg_roas_high": sum(c["roas"] for c in high_performers) / len(high_performers) if high_performers else 0,
                    "avg_roas_low": sum(c["roas"] for c in low_performers) / len(low_performers) if low_performers else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to optimize budgets: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def apply_budget_changes(self, recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Apply approved budget changes
        
        Args:
            recommendations: List of budget change recommendations
        
        Returns:
            Application results
        """
        try:
            from agents.ads import ads_agent
            
            applied = []
            failed = []
            
            for rec in recommendations:
                try:
                    # Update campaign budget via Google Ads API
                    result = await ads_agent.update_campaign_budget(
                        rec["campaign_id"],
                        rec["recommended_budget"]
                    )
                    
                    if result.get("success"):
                        applied.append(rec)
                    else:
                        failed.append({
                            **rec,
                            "error": result.get("error")
                        })
                        
                except Exception as e:
                    failed.append({
                        **rec,
                        "error": str(e)
                    })
            
            return {
                "success": True,
                "applied": len(applied),
                "failed": len(failed),
                "applied_changes": applied,
                "failed_changes": failed
            }
            
        except Exception as e:
            logger.error(f"Failed to apply budget changes: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_optimization_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get budget optimization history"""
        try:
            sb = self._get_sb()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            result = sb.table("budget_optimizations")\
                .select("*")\
                .gte("optimized_at", cutoff)\
                .order("optimized_at", desc=True)\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"Failed to get optimization history: {e}")
            return []


# Global instance
budget_optimizer = BudgetOptimizer()
