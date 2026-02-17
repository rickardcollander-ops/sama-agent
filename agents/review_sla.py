"""
Review SLA Tracking
Monitors response times and ensures SLA compliance
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging

from shared.database import get_supabase
from shared.alerts import alert_system, Alert, AlertType, AlertSeverity

logger = logging.getLogger(__name__)


class ReviewSLATracker:
    """Track and enforce review response SLAs"""
    
    # SLA thresholds in hours
    SLA_THRESHOLDS = {
        "5_star": 24,      # 24 hours for 5-star reviews
        "4_star": 12,      # 12 hours for 4-star reviews
        "3_star": 6,       # 6 hours for 3-star reviews
        "2_star": 3,       # 3 hours for 2-star reviews
        "1_star": 2,       # 2 hours for 1-star reviews (critical)
    }
    
    def __init__(self):
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    def _get_sla_category(self, rating: int) -> str:
        """Get SLA category based on rating"""
        if rating == 5:
            return "5_star"
        elif rating == 4:
            return "4_star"
        elif rating == 3:
            return "3_star"
        elif rating == 2:
            return "2_star"
        else:
            return "1_star"
    
    async def check_sla_compliance(self) -> Dict[str, Any]:
        """
        Check all pending reviews for SLA compliance
        
        Returns:
            Summary of SLA status and violations
        """
        try:
            sb = self._get_sb()
            
            # Get all reviews without responses
            result = sb.table("reviews")\
                .select("*")\
                .is_("response_sent_at", None)\
                .execute()
            
            pending_reviews = result.data if result.data else []
            
            violations = []
            warnings = []
            compliant = []
            
            now = datetime.utcnow()
            
            for review in pending_reviews:
                rating = review.get("rating", 3)
                scraped_at = datetime.fromisoformat(review.get("scraped_at", now.isoformat()))
                
                # Calculate time elapsed
                elapsed_hours = (now - scraped_at).total_seconds() / 3600
                
                # Get SLA threshold
                sla_category = self._get_sla_category(rating)
                sla_threshold = self.SLA_THRESHOLDS[sla_category]
                
                # Calculate remaining time
                remaining_hours = sla_threshold - elapsed_hours
                
                review_info = {
                    "review_id": review.get("id"),
                    "platform": review.get("platform"),
                    "rating": rating,
                    "elapsed_hours": round(elapsed_hours, 1),
                    "sla_threshold": sla_threshold,
                    "remaining_hours": round(remaining_hours, 1),
                    "status": "compliant"
                }
                
                if remaining_hours < 0:
                    # SLA violated
                    review_info["status"] = "violated"
                    violations.append(review_info)
                    
                    # Create alert
                    await alert_system.send_alert(Alert(
                        type=AlertType.REVIEW_NEGATIVE if rating <= 2 else AlertType.APPROVAL_NEEDED,
                        severity=AlertSeverity.CRITICAL if rating <= 2 else AlertSeverity.WARNING,
                        title=f"Review SLA Violated: {review.get('platform')}",
                        message=f"{rating}-star review exceeded {sla_threshold}h SLA by {abs(remaining_hours):.1f}h",
                        data={
                            "review_id": review.get("id"),
                            "platform": review.get("platform"),
                            "rating": rating,
                            "elapsed_hours": elapsed_hours,
                            "sla_threshold": sla_threshold
                        },
                        agent="review_agent",
                        requires_approval=True
                    ))
                    
                elif remaining_hours < 2:
                    # Warning: approaching SLA
                    review_info["status"] = "warning"
                    warnings.append(review_info)
                else:
                    # Compliant
                    compliant.append(review_info)
            
            # Calculate SLA metrics
            total_reviews = len(pending_reviews)
            sla_compliance_rate = (len(compliant) / total_reviews * 100) if total_reviews > 0 else 100
            
            # Save SLA report
            sb.table("review_sla_reports").insert({
                "total_pending": total_reviews,
                "violations": len(violations),
                "warnings": len(warnings),
                "compliant": len(compliant),
                "compliance_rate": sla_compliance_rate,
                "checked_at": now.isoformat()
            }).execute()
            
            return {
                "success": True,
                "total_pending": total_reviews,
                "violations": violations,
                "warnings": warnings,
                "compliant": compliant,
                "compliance_rate": round(sla_compliance_rate, 1),
                "summary": {
                    "violated": len(violations),
                    "at_risk": len(warnings),
                    "on_track": len(compliant)
                }
            }
            
        except Exception as e:
            logger.error(f"SLA compliance check failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_sla_stats(self, days: int = 30) -> Dict[str, Any]:
        """
        Get SLA statistics for the past N days
        
        Args:
            days: Number of days to analyze
        """
        try:
            sb = self._get_sb()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            # Get all reviews from period
            result = sb.table("reviews")\
                .select("*")\
                .gte("scraped_at", cutoff)\
                .execute()
            
            reviews = result.data if result.data else []
            
            # Calculate response times
            response_times = []
            for review in reviews:
                if review.get("response_sent_at"):
                    scraped_at = datetime.fromisoformat(review.get("scraped_at"))
                    responded_at = datetime.fromisoformat(review.get("response_sent_at"))
                    response_time = (responded_at - scraped_at).total_seconds() / 3600
                    
                    rating = review.get("rating", 3)
                    sla_category = self._get_sla_category(rating)
                    sla_threshold = self.SLA_THRESHOLDS[sla_category]
                    
                    response_times.append({
                        "rating": rating,
                        "response_time_hours": response_time,
                        "sla_threshold": sla_threshold,
                        "met_sla": response_time <= sla_threshold
                    })
            
            # Calculate metrics
            total_responded = len(response_times)
            met_sla = sum(1 for rt in response_times if rt["met_sla"])
            sla_compliance = (met_sla / total_responded * 100) if total_responded > 0 else 0
            
            avg_response_time = sum(rt["response_time_hours"] for rt in response_times) / total_responded if total_responded > 0 else 0
            
            # By rating
            by_rating = {}
            for rating in [1, 2, 3, 4, 5]:
                rating_responses = [rt for rt in response_times if rt["rating"] == rating]
                if rating_responses:
                    by_rating[f"{rating}_star"] = {
                        "count": len(rating_responses),
                        "avg_response_time": sum(rt["response_time_hours"] for rt in rating_responses) / len(rating_responses),
                        "sla_compliance": sum(1 for rt in rating_responses if rt["met_sla"]) / len(rating_responses) * 100
                    }
            
            return {
                "success": True,
                "period_days": days,
                "total_reviews": len(reviews),
                "total_responded": total_responded,
                "sla_compliance_rate": round(sla_compliance, 1),
                "avg_response_time_hours": round(avg_response_time, 1),
                "by_rating": by_rating,
                "sla_thresholds": self.SLA_THRESHOLDS
            }
            
        except Exception as e:
            logger.error(f"Failed to get SLA stats: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def mark_review_responded(self, review_id: str) -> Dict[str, Any]:
        """
        Mark a review as responded and calculate response time
        
        Args:
            review_id: Review ID
        """
        try:
            sb = self._get_sb()
            
            # Get review
            result = sb.table("reviews")\
                .select("*")\
                .eq("id", review_id)\
                .single()\
                .execute()
            
            if not result.data:
                return {"success": False, "error": "Review not found"}
            
            review = result.data
            scraped_at = datetime.fromisoformat(review.get("scraped_at"))
            responded_at = datetime.utcnow()
            
            response_time_hours = (responded_at - scraped_at).total_seconds() / 3600
            
            # Check SLA
            rating = review.get("rating", 3)
            sla_category = self._get_sla_category(rating)
            sla_threshold = self.SLA_THRESHOLDS[sla_category]
            met_sla = response_time_hours <= sla_threshold
            
            # Update review
            sb.table("reviews")\
                .update({
                    "response_sent_at": responded_at.isoformat(),
                    "response_time_hours": response_time_hours,
                    "met_sla": met_sla
                })\
                .eq("id", review_id)\
                .execute()
            
            return {
                "success": True,
                "review_id": review_id,
                "response_time_hours": round(response_time_hours, 1),
                "sla_threshold": sla_threshold,
                "met_sla": met_sla
            }
            
        except Exception as e:
            logger.error(f"Failed to mark review responded: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Global instance
review_sla_tracker = ReviewSLATracker()
