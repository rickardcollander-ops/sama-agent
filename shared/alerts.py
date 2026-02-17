"""
Alert System for SAMA 2.0
Handles notifications for critical events, budget changes, performance issues
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime
from pydantic import BaseModel
import logging

from shared.database import get_supabase

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    BUDGET_CHANGE = "budget_change"
    CPC_SPIKE = "cpc_spike"
    KEYWORD_DROP = "keyword_drop"
    REVIEW_NEGATIVE = "review_negative"
    PERFORMANCE_DROP = "performance_drop"
    APPROVAL_NEEDED = "approval_needed"
    TASK_FAILED = "task_failed"


class Alert(BaseModel):
    type: AlertType
    severity: AlertSeverity
    title: str
    message: str
    data: Optional[Dict[str, Any]] = None
    agent: str
    requires_approval: bool = False
    timestamp: datetime = None
    
    def __init__(self, **data):
        if 'timestamp' not in data:
            data['timestamp'] = datetime.utcnow()
        super().__init__(**data)


class AlertSystem:
    """Central alert management system"""
    
    def __init__(self):
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def send_alert(self, alert: Alert) -> Dict[str, Any]:
        """
        Send an alert and store in database
        In production, this would also send emails/Slack/Discord notifications
        """
        try:
            sb = self._get_sb()
            
            # Store alert in database
            alert_data = {
                "type": alert.type.value,
                "severity": alert.severity.value,
                "title": alert.title,
                "message": alert.message,
                "data": alert.data or {},
                "agent": alert.agent,
                "requires_approval": alert.requires_approval,
                "timestamp": alert.timestamp.isoformat(),
                "status": "pending" if alert.requires_approval else "sent",
                "created_at": datetime.utcnow().isoformat()
            }
            
            result = sb.table("alerts").insert(alert_data).execute()
            
            # Log alert
            log_level = {
                AlertSeverity.INFO: logging.INFO,
                AlertSeverity.WARNING: logging.WARNING,
                AlertSeverity.CRITICAL: logging.ERROR
            }
            logger.log(
                log_level[alert.severity],
                f"[{alert.agent}] {alert.title}: {alert.message}"
            )
            
            # TODO: Send to external notification channels
            # - Email via SendGrid
            # - Slack webhook
            # - Discord webhook
            
            return {
                "success": True,
                "alert_id": result.data[0]["id"] if result.data else None,
                "message": "Alert sent successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Get all alerts requiring approval"""
        try:
            sb = self._get_sb()
            result = sb.table("alerts")\
                .select("*")\
                .eq("requires_approval", True)\
                .eq("status", "pending")\
                .order("created_at", desc=True)\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"Failed to get pending approvals: {e}")
            return []
    
    async def approve_alert(self, alert_id: str, approved_by: str) -> Dict[str, Any]:
        """Approve a pending alert"""
        try:
            sb = self._get_sb()
            result = sb.table("alerts")\
                .update({
                    "status": "approved",
                    "approved_by": approved_by,
                    "approved_at": datetime.utcnow().isoformat()
                })\
                .eq("id", alert_id)\
                .execute()
            
            return {
                "success": True,
                "message": "Alert approved"
            }
            
        except Exception as e:
            logger.error(f"Failed to approve alert: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def reject_alert(self, alert_id: str, rejected_by: str, reason: str) -> Dict[str, Any]:
        """Reject a pending alert"""
        try:
            sb = self._get_sb()
            result = sb.table("alerts")\
                .update({
                    "status": "rejected",
                    "rejected_by": rejected_by,
                    "rejected_at": datetime.utcnow().isoformat(),
                    "rejection_reason": reason
                })\
                .eq("id", alert_id)\
                .execute()
            
            return {
                "success": True,
                "message": "Alert rejected"
            }
            
        except Exception as e:
            logger.error(f"Failed to reject alert: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def check_cpc_spike(self, current_cpc: float, avg_cpc: float, threshold: float = 0.3) -> Optional[Alert]:
        """Check if CPC has spiked above threshold"""
        if current_cpc > avg_cpc * (1 + threshold):
            spike_percentage = ((current_cpc - avg_cpc) / avg_cpc) * 100
            return Alert(
                type=AlertType.CPC_SPIKE,
                severity=AlertSeverity.WARNING,
                title="CPC Spike Detected",
                message=f"CPC increased by {spike_percentage:.1f}% (${avg_cpc:.2f} → ${current_cpc:.2f})",
                data={
                    "current_cpc": current_cpc,
                    "avg_cpc": avg_cpc,
                    "spike_percentage": spike_percentage
                },
                agent="ads_agent"
            )
        return None
    
    async def check_keyword_drop(self, keyword: str, old_position: float, new_position: float, threshold: int = 5) -> Optional[Alert]:
        """Check if keyword position dropped significantly"""
        if new_position > old_position + threshold:
            drop = new_position - old_position
            return Alert(
                type=AlertType.KEYWORD_DROP,
                severity=AlertSeverity.WARNING,
                title=f"Keyword Position Drop: {keyword}",
                message=f"Position dropped from {old_position:.1f} to {new_position:.1f} (-{drop:.1f} positions)",
                data={
                    "keyword": keyword,
                    "old_position": old_position,
                    "new_position": new_position,
                    "drop": drop
                },
                agent="seo_agent"
            )
        return None
    
    async def check_budget_change(self, campaign: str, old_budget: float, new_budget: float, threshold: float = 0.3) -> Optional[Alert]:
        """Check if budget change requires approval"""
        change_percentage = abs((new_budget - old_budget) / old_budget)
        
        if change_percentage > threshold:
            return Alert(
                type=AlertType.BUDGET_CHANGE,
                severity=AlertSeverity.CRITICAL,
                title=f"Budget Change Approval Required: {campaign}",
                message=f"Budget change of {change_percentage*100:.1f}% (${old_budget:.2f} → ${new_budget:.2f})",
                data={
                    "campaign": campaign,
                    "old_budget": old_budget,
                    "new_budget": new_budget,
                    "change_percentage": change_percentage
                },
                agent="ads_agent",
                requires_approval=True
            )
        return None
    
    async def check_negative_review(self, platform: str, rating: int, review_text: str) -> Optional[Alert]:
        """Alert on negative reviews"""
        if rating <= 2:
            return Alert(
                type=AlertType.REVIEW_NEGATIVE,
                severity=AlertSeverity.CRITICAL,
                title=f"Negative Review on {platform}",
                message=f"{rating}-star review: {review_text[:100]}...",
                data={
                    "platform": platform,
                    "rating": rating,
                    "review_text": review_text
                },
                agent="review_agent",
                requires_approval=True  # Human should respond
            )
        return None


# Global alert system instance
alert_system = AlertSystem()
