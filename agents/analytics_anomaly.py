"""
Anomaly Detection for Analytics Agent
Automatically detects unusual patterns in traffic, conversions, and spend
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import statistics
import logging

from shared.database import get_supabase
from shared.alerts import alert_system, Alert, AlertType, AlertSeverity

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detect anomalies in marketing metrics"""
    
    # Thresholds
    ANOMALY_THRESHOLD = 0.30  # 30% deviation from baseline
    MIN_DATA_POINTS = 7  # Minimum days of data needed
    
    def __init__(self):
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def detect_traffic_anomalies(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        Detect traffic anomalies
        
        Args:
            days: Number of days to analyze
        
        Returns:
            List of detected anomalies
        """
        try:
            sb = self._get_sb()
            
            # Get daily traffic data
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            result = sb.table("daily_metrics")\
                .select("date, total_sessions, total_pageviews")\
                .gte("date", cutoff)\
                .order("date")\
                .execute()
            
            data = result.data if result.data else []
            
            if len(data) < self.MIN_DATA_POINTS:
                return []
            
            anomalies = []
            
            # Analyze sessions
            sessions = [d["total_sessions"] for d in data[:-1]]  # Exclude today
            today_sessions = data[-1]["total_sessions"]
            
            session_anomaly = self._detect_anomaly(sessions, today_sessions, "sessions")
            if session_anomaly:
                anomalies.append(session_anomaly)
            
            # Analyze pageviews
            pageviews = [d["total_pageviews"] for d in data[:-1]]
            today_pageviews = data[-1]["total_pageviews"]
            
            pageview_anomaly = self._detect_anomaly(pageviews, today_pageviews, "pageviews")
            if pageview_anomaly:
                anomalies.append(pageview_anomaly)
            
            # Create alerts for anomalies
            for anomaly in anomalies:
                await self._create_anomaly_alert(anomaly)
            
            return anomalies
            
        except Exception as e:
            logger.error(f"Failed to detect traffic anomalies: {e}")
            return []
    
    async def detect_conversion_anomalies(self, days: int = 30) -> List[Dict[str, Any]]:
        """Detect conversion rate anomalies"""
        try:
            sb = self._get_sb()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            result = sb.table("daily_metrics")\
                .select("date, total_conversions, total_sessions")\
                .gte("date", cutoff)\
                .order("date")\
                .execute()
            
            data = result.data if result.data else []
            
            if len(data) < self.MIN_DATA_POINTS:
                return []
            
            # Calculate conversion rates
            conversion_rates = []
            for d in data[:-1]:
                if d["total_sessions"] > 0:
                    cr = (d["total_conversions"] / d["total_sessions"]) * 100
                    conversion_rates.append(cr)
            
            today_data = data[-1]
            today_cr = (today_data["total_conversions"] / today_data["total_sessions"]) * 100 if today_data["total_sessions"] > 0 else 0
            
            anomaly = self._detect_anomaly(conversion_rates, today_cr, "conversion_rate")
            
            if anomaly:
                await self._create_anomaly_alert(anomaly)
                return [anomaly]
            
            return []
            
        except Exception as e:
            logger.error(f"Failed to detect conversion anomalies: {e}")
            return []
    
    async def detect_spend_anomalies(self, days: int = 30) -> List[Dict[str, Any]]:
        """Detect ad spend anomalies"""
        try:
            sb = self._get_sb()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            result = sb.table("daily_metrics")\
                .select("date, total_ad_spend")\
                .gte("date", cutoff)\
                .order("date")\
                .execute()
            
            data = result.data if result.data else []
            
            if len(data) < self.MIN_DATA_POINTS:
                return []
            
            spend_values = [d["total_ad_spend"] for d in data[:-1]]
            today_spend = data[-1]["total_ad_spend"]
            
            anomaly = self._detect_anomaly(spend_values, today_spend, "ad_spend")
            
            if anomaly:
                await self._create_anomaly_alert(anomaly)
                return [anomaly]
            
            return []
            
        except Exception as e:
            logger.error(f"Failed to detect spend anomalies: {e}")
            return []
    
    def _detect_anomaly(
        self,
        historical_values: List[float],
        current_value: float,
        metric_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Detect if current value is anomalous compared to historical baseline
        
        Args:
            historical_values: List of historical values
            current_value: Current value to check
            metric_name: Name of the metric
        
        Returns:
            Anomaly details if detected, None otherwise
        """
        if len(historical_values) < self.MIN_DATA_POINTS:
            return None
        
        # Calculate baseline statistics
        mean = statistics.mean(historical_values)
        stdev = statistics.stdev(historical_values) if len(historical_values) > 1 else 0
        
        # Calculate deviation
        deviation = abs(current_value - mean)
        deviation_pct = (deviation / mean) if mean > 0 else 0
        
        # Check if anomalous (using both absolute and relative thresholds)
        is_anomalous = False
        anomaly_type = None
        severity = AlertSeverity.INFO
        
        # Statistical anomaly (>2 standard deviations)
        if stdev > 0 and deviation > (2 * stdev):
            is_anomalous = True
            anomaly_type = "statistical"
            severity = AlertSeverity.WARNING
        
        # Percentage anomaly (>30% change)
        if deviation_pct > self.ANOMALY_THRESHOLD:
            is_anomalous = True
            anomaly_type = "percentage"
            severity = AlertSeverity.CRITICAL if deviation_pct > 0.5 else AlertSeverity.WARNING
        
        if not is_anomalous:
            return None
        
        # Determine direction
        direction = "increase" if current_value > mean else "decrease"
        
        return {
            "metric": metric_name,
            "current_value": current_value,
            "baseline_mean": mean,
            "baseline_stdev": stdev,
            "deviation": deviation,
            "deviation_percentage": deviation_pct * 100,
            "direction": direction,
            "anomaly_type": anomaly_type,
            "severity": severity.value,
            "detected_at": datetime.utcnow().isoformat()
        }
    
    async def _create_anomaly_alert(self, anomaly: Dict[str, Any]) -> None:
        """Create alert for detected anomaly"""
        metric = anomaly["metric"]
        direction = anomaly["direction"]
        deviation_pct = anomaly["deviation_percentage"]
        current = anomaly["current_value"]
        baseline = anomaly["baseline_mean"]
        
        severity_map = {
            "info": AlertSeverity.INFO,
            "warning": AlertSeverity.WARNING,
            "critical": AlertSeverity.CRITICAL
        }
        
        await alert_system.send_alert(Alert(
            type=AlertType.PERFORMANCE_DROP if direction == "decrease" else AlertType.APPROVAL_NEEDED,
            severity=severity_map.get(anomaly["severity"], AlertSeverity.WARNING),
            title=f"Anomaly Detected: {metric.replace('_', ' ').title()}",
            message=f"{metric.replace('_', ' ').title()} {direction}d by {deviation_pct:.1f}% ({baseline:.1f} → {current:.1f})",
            data=anomaly,
            agent="analytics_agent",
            requires_approval=False
        ))
    
    async def analyze_root_cause(self, anomaly: Dict[str, Any]) -> Dict[str, Any]:
        """
        Attempt to identify root cause of anomaly
        
        Args:
            anomaly: Anomaly details
        
        Returns:
            Root cause analysis
        """
        try:
            metric = anomaly["metric"]
            direction = anomaly["direction"]
            
            # Get related metrics to find correlations
            sb = self._get_sb()
            
            today = datetime.utcnow().date().isoformat()
            
            result = sb.table("daily_metrics")\
                .select("*")\
                .eq("date", today)\
                .single()\
                .execute()
            
            if not result.data:
                return {"root_cause": "Unknown - insufficient data"}
            
            data = result.data
            
            # Analyze potential causes based on metric
            potential_causes = []
            
            if metric == "sessions" and direction == "decrease":
                # Check if organic traffic dropped
                if data.get("organic_sessions", 0) < data.get("avg_organic_sessions", 0) * 0.7:
                    potential_causes.append("Organic traffic drop - possible SEO issue or Google algorithm update")
                
                # Check if paid traffic dropped
                if data.get("paid_sessions", 0) < data.get("avg_paid_sessions", 0) * 0.7:
                    potential_causes.append("Paid traffic drop - check if campaigns are paused or budget depleted")
            
            elif metric == "conversion_rate" and direction == "decrease":
                # Check if specific channel underperforming
                if data.get("landing_page_conversion_rate", 0) < 1.0:
                    potential_causes.append("Landing page issue - check for technical problems or poor UX")
                
                # Check if form submissions down
                if data.get("form_submissions", 0) < data.get("avg_form_submissions", 0) * 0.7:
                    potential_causes.append("Form submission issue - possible technical problem with forms")
            
            elif metric == "ad_spend" and direction == "increase":
                # Check if CPC increased
                if data.get("avg_cpc", 0) > data.get("baseline_cpc", 0) * 1.3:
                    potential_causes.append("CPC spike - increased competition or decreased Quality Score")
            
            if not potential_causes:
                potential_causes.append("No obvious root cause identified - manual investigation recommended")
            
            return {
                "metric": metric,
                "direction": direction,
                "potential_causes": potential_causes,
                "recommendation": self._generate_recommendation(metric, direction, potential_causes)
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze root cause: {e}")
            return {"root_cause": "Analysis failed", "error": str(e)}
    
    def _generate_recommendation(
        self,
        metric: str,
        direction: str,
        causes: List[str]
    ) -> str:
        """Generate actionable recommendation based on anomaly"""
        if metric == "sessions" and direction == "decrease":
            return "1. Check Google Search Console for indexing issues\n2. Verify all ad campaigns are active\n3. Check for website downtime or technical issues"
        
        elif metric == "conversion_rate" and direction == "decrease":
            return "1. Test landing pages for technical issues\n2. Review recent website changes\n3. Check form functionality\n4. Analyze user session recordings"
        
        elif metric == "ad_spend" and direction == "increase":
            return "1. Review Quality Scores for all keywords\n2. Check for bid strategy changes\n3. Analyze competitor activity\n4. Consider pausing underperforming campaigns"
        
        return "Manual investigation recommended to identify root cause"
    
    async def get_anomaly_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get historical anomalies"""
        try:
            sb = self._get_sb()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            result = sb.table("detected_anomalies")\
                .select("*")\
                .gte("detected_at", cutoff)\
                .order("detected_at", desc=True)\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"Failed to get anomaly history: {e}")
            return []


# Global instance
anomaly_detector = AnomalyDetector()
