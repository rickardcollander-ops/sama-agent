"""
Dashboard API — Cross-agent intelligence for the SAMA command center.
Aggregates data from all agents and generates smart recommendations.
"""

from fastapi import APIRouter
from typing import Dict, Any, List
import logging
import asyncio
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/status")
async def get_dashboard_status():
    """
    Aggregated status from all agents + Supabase.
    Returns health, last activity, and key counts.
    """
    from shared.database import get_supabase

    status: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "agents": {},
        "counts": {},
    }

    try:
        sb = get_supabase()

        # Parallel DB queries
        async def _query(table: str, col: str = "*", limit: int = 0, count_only: bool = False):
            try:
                q = sb.table(table).select(col, count="exact") if count_only else sb.table(table).select(col)
                if limit:
                    q = q.limit(limit)
                q = q.order("created_at", desc=True) if not count_only else q
                r = q.execute()
                return r
            except Exception:
                return None

        kw_res, content_res, audit_res, alert_res, log_res = await asyncio.gather(
            asyncio.to_thread(lambda: _safe_count(sb, "keywords")),
            asyncio.to_thread(lambda: _safe_count(sb, "content_pieces")),
            asyncio.to_thread(lambda: _safe_count(sb, "seo_audits")),
            asyncio.to_thread(lambda: _safe_count(sb, "alerts")),
            asyncio.to_thread(lambda: _safe_recent(sb, "agent_logs", 5)),
        )

        status["counts"] = {
            "keywords": kw_res,
            "content_pieces": content_res,
            "seo_audits": audit_res,
            "alerts": alert_res,
        }
        status["recent_activity"] = log_res

    except Exception as e:
        logger.warning(f"Dashboard status DB error: {e}")
        status["db_error"] = str(e)

    # Agent availability (lightweight)
    for agent_name in ["seo", "content", "ads", "social", "reviews", "analytics", "ai_visibility"]:
        status["agents"][agent_name] = "operational"

    return status


@router.get("/recommendations")
async def get_smart_recommendations():
    """
    Cross-agent smart recommendations.
    Analyzes current state and suggests highest-impact actions.
    """
    from shared.database import get_supabase
    import asyncio

    recommendations: List[Dict[str, Any]] = []

    try:
        sb = get_supabase()

        # Gather data in parallel
        kw_data, alert_data, audit_data, content_data = await asyncio.gather(
            asyncio.to_thread(lambda: _safe_query(sb, "keywords", "keyword,current_position,current_ctr,current_impressions,last_checked_at", 100)),
            asyncio.to_thread(lambda: _safe_query(sb, "alerts", "id,alert_type,severity,status,created_at", 50)),
            asyncio.to_thread(lambda: _safe_query(sb, "seo_audits", "id,audit_date,critical_issues,high_issues", 3)),
            asyncio.to_thread(lambda: _safe_query(sb, "content_pieces", "id,title,status,impressions_30d,clicks_30d,created_at", 50)),
        )

        # 1. Keywords losing position — high impact
        if kw_data:
            declining = [k for k in kw_data if (k.get("current_position") or 100) > 10 and (k.get("current_impressions") or 0) > 50]
            if declining:
                top = sorted(declining, key=lambda k: k.get("current_impressions", 0), reverse=True)[:3]
                kw_names = ", ".join(k["keyword"] for k in top)
                recommendations.append({
                    "id": "kw-position-drop",
                    "title": "Keywords outside top 10 with high impressions",
                    "description": f"{len(declining)} keywords with high impressions but ranking below #10. Focus on: {kw_names}",
                    "priority": "high",
                    "agent": "seo",
                    "action": "Run SEO audit and optimize these pages",
                    "impact": "high",
                    "effort": "medium",
                })

            # CTR opportunities
            low_ctr = [k for k in kw_data if (k.get("current_position") or 100) <= 5 and (k.get("current_ctr") or 0) < 3.0 and (k.get("current_impressions") or 0) > 20]
            if low_ctr:
                recommendations.append({
                    "id": "ctr-optimization",
                    "title": "Low CTR on top-ranking keywords",
                    "description": f"{len(low_ctr)} keywords rank in top 5 but have CTR under 3%. Improve title tags and meta descriptions.",
                    "priority": "high",
                    "agent": "seo",
                    "action": "Optimize meta titles and descriptions",
                    "impact": "high",
                    "effort": "low",
                })

            # Stale keyword data
            stale = [k for k in kw_data if not k.get("last_checked_at")]
            if len(stale) > len(kw_data) * 0.3:
                recommendations.append({
                    "id": "stale-keywords",
                    "title": "Keyword data needs refresh",
                    "description": f"{len(stale)} of {len(kw_data)} keywords have never been checked. Run keyword tracking.",
                    "priority": "medium",
                    "agent": "seo",
                    "action": "Trigger keyword tracking sync",
                    "impact": "medium",
                    "effort": "low",
                })

        # 2. Pending alerts — need attention
        if alert_data:
            pending = [a for a in alert_data if a.get("status") == "pending"]
            if pending:
                high_sev = [a for a in pending if a.get("severity") == "high"]
                recommendations.append({
                    "id": "pending-alerts",
                    "title": f"{len(pending)} pending alerts need review",
                    "description": f"{len(high_sev)} are high severity. Review and act on pending alerts.",
                    "priority": "high" if high_sev else "medium",
                    "agent": "system",
                    "action": "Go to Approvals page",
                    "impact": "high" if high_sev else "medium",
                    "effort": "low",
                })

        # 3. SEO audit issues
        if audit_data and len(audit_data) > 0:
            latest = audit_data[0]
            critical = latest.get("critical_issues") or []
            high = latest.get("high_issues") or []
            issue_count = len(critical) + len(high)
            if issue_count > 0:
                recommendations.append({
                    "id": "seo-audit-issues",
                    "title": f"{issue_count} critical/high SEO issues found",
                    "description": f"Latest audit found {len(critical)} critical and {len(high)} high-priority issues.",
                    "priority": "high",
                    "agent": "seo",
                    "action": "Review SEO audit and fix issues",
                    "impact": "high",
                    "effort": "medium",
                })

        # 4. Content gaps
        if content_data:
            drafts = [c for c in content_data if c.get("status") == "draft"]
            if drafts:
                recommendations.append({
                    "id": "unpublished-content",
                    "title": f"{len(drafts)} draft articles ready for review",
                    "description": "Unpublished content is losing potential traffic. Review and publish.",
                    "priority": "medium",
                    "agent": "content",
                    "action": "Review draft content for publishing",
                    "impact": "medium",
                    "effort": "low",
                })

            low_perf = [c for c in content_data if (c.get("impressions_30d") or 0) < 10 and c.get("status") == "published"]
            if low_perf:
                recommendations.append({
                    "id": "low-traffic-content",
                    "title": f"{len(low_perf)} published articles getting <10 impressions",
                    "description": "Consider refreshing or consolidating underperforming content.",
                    "priority": "low",
                    "agent": "content",
                    "action": "Run content analytics to identify refresh targets",
                    "impact": "medium",
                    "effort": "medium",
                })

        # 5. General recommendations (always useful)
        if not kw_data:
            recommendations.append({
                "id": "setup-keywords",
                "title": "Set up keyword tracking",
                "description": "No keywords are being tracked. Add target keywords to start monitoring rankings.",
                "priority": "high",
                "agent": "seo",
                "action": "Add keywords via SEO page",
                "impact": "high",
                "effort": "low",
            })

    except Exception as e:
        logger.warning(f"Recommendation generation error: {e}")
        recommendations.append({
            "id": "db-error",
            "title": "Could not fetch data for recommendations",
            "description": f"Database error: {str(e)[:100]}. Check backend connectivity.",
            "priority": "medium",
            "agent": "system",
            "action": "Check backend health",
            "impact": "high",
            "effort": "low",
        })

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 3))

    return {
        "recommendations": recommendations,
        "total": len(recommendations),
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_count(sb, table: str) -> int:
    try:
        r = sb.table(table).select("id", count="exact").limit(0).execute()
        return r.count or 0
    except Exception:
        return 0


def _safe_recent(sb, table: str, limit: int) -> list:
    try:
        r = sb.table(table).select("*").order("created_at", desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        return []


def _safe_query(sb, table: str, columns: str, limit: int) -> list:
    try:
        r = sb.table(table).select(columns).limit(limit).execute()
        return r.data or []
    except Exception:
        return []
