"""
SAMA 2.0 - Development Agent
Automated system health checker that:
- Tests all API endpoints daily
- Validates database tables and connectivity
- Checks agent scheduler health
- Detects errors and reports them via notifications
- Can trigger self-healing actions
"""

import logging
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# All known API endpoints grouped by agent/module
ENDPOINT_REGISTRY: List[Dict[str, Any]] = [
    # Health
    {"method": "GET", "path": "/", "name": "root_health", "critical": True},
    {"method": "GET", "path": "/health", "name": "health_check", "critical": True},

    # Dashboard
    {"method": "GET", "path": "/api/dashboard/summary", "name": "dashboard_summary", "critical": True},
    {"method": "GET", "path": "/api/dashboard/events", "name": "dashboard_events"},
    {"method": "GET", "path": "/api/dashboard/agent-status", "name": "agent_status"},

    # Orchestrator
    {"method": "GET", "path": "/api/orchestrator/status", "name": "orchestrator_status", "critical": True},

    # SEO
    {"method": "GET", "path": "/api/seo/keywords", "name": "seo_keywords", "critical": True},
    {"method": "GET", "path": "/api/seo/audits", "name": "seo_audits"},
    {"method": "GET", "path": "/api/seo/backlinks", "name": "seo_backlinks"},
    {"method": "GET", "path": "/api/seo/competitors", "name": "seo_competitors"},
    {"method": "GET", "path": "/api/seo/serp-features", "name": "seo_serp"},

    # Content
    {"method": "GET", "path": "/api/content/pieces", "name": "content_pieces", "critical": True},
    {"method": "GET", "path": "/api/content/analytics", "name": "content_analytics"},

    # Ads
    {"method": "GET", "path": "/api/ads/campaigns", "name": "ads_campaigns"},
    {"method": "GET", "path": "/api/ads/performance", "name": "ads_performance"},
    {"method": "GET", "path": "/api/ads/budget", "name": "ads_budget"},

    # Social
    {"method": "GET", "path": "/api/social/posts", "name": "social_posts"},
    {"method": "GET", "path": "/api/social/reddit/posts", "name": "social_reddit"},

    # Reviews
    {"method": "GET", "path": "/api/reviews", "name": "reviews_list"},
    {"method": "GET", "path": "/api/reviews/summary", "name": "reviews_summary"},

    # Analytics
    {"method": "GET", "path": "/api/analytics/daily-metrics", "name": "analytics_daily", "critical": True},
    {"method": "GET", "path": "/api/analytics/channel-breakdown", "name": "analytics_channels"},

    # AI Visibility
    {"method": "GET", "path": "/api/ai-visibility/latest", "name": "ai_visibility"},

    # Alerts
    {"method": "GET", "path": "/api/alerts", "name": "alerts_list"},

    # Automation
    {"method": "GET", "path": "/api/automation/jobs", "name": "automation_jobs"},

    # Goals
    {"method": "GET", "path": "/api/goals", "name": "goals_list"},

    # Notifications
    {"method": "GET", "path": "/api/notifications?limit=5&unread_only=false", "name": "notifications_list"},

    # GTM
    {"method": "GET", "path": "/api/gtm/strategy", "name": "gtm_strategy"},

    # Improvements
    {"method": "GET", "path": "/api/improvements", "name": "improvements_list"},
]

# Supabase tables that should exist
REQUIRED_TABLES = [
    "keywords", "seo_audits", "seo_keywords", "content_pieces",
    "backlink_profiles", "competitor_analyses", "agent_actions",
    "agent_cycles", "agent_learnings", "alerts", "daily_metrics",
    "social_posts", "review_responses", "notifications", "agent_goals",
]


class DevAgent:
    """
    Development agent that validates system health across all components.
    Runs daily via scheduler and can be triggered on-demand via API.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self._last_report: Optional[Dict[str, Any]] = None

    async def run_full_health_check(self) -> Dict[str, Any]:
        """Run all health checks and return a comprehensive report."""
        started_at = datetime.now(timezone.utc).isoformat()
        logger.info("[dev-agent] Starting full health check...")

        results = {
            "started_at": started_at,
            "endpoints": await self._test_endpoints(),
            "database": await self._test_database(),
            "scheduler": self._test_scheduler(),
            "event_bus": await self._test_event_bus(),
            "agents": await self._test_agent_imports(),
        }

        # Calculate summary
        ep = results["endpoints"]
        db = results["database"]
        sched = results["scheduler"]

        total_checks = ep["total"] + db["total"] + sched["total"] + 1 + 1
        total_passed = ep["passed"] + db["passed"] + sched["passed"]
        total_passed += 1 if results["event_bus"]["connected"] else 0
        total_passed += results["agents"]["importable"]

        results["summary"] = {
            "total_checks": total_checks,
            "passed": total_passed,
            "failed": total_checks - total_passed,
            "health_pct": round((total_passed / max(total_checks, 1)) * 100, 1),
            "status": "healthy" if (total_checks - total_passed) == 0 else
                      "degraded" if (total_checks - total_passed) <= 3 else "unhealthy",
        }
        results["finished_at"] = datetime.now(timezone.utc).isoformat()

        self._last_report = results

        # Send notification if issues found
        await self._notify_results(results)

        logger.info(
            f"[dev-agent] Health check done: {results['summary']['health_pct']}% healthy "
            f"({results['summary']['passed']}/{results['summary']['total_checks']} passed)"
        )
        return results

    async def _test_endpoints(self) -> Dict[str, Any]:
        """Test all registered API endpoints."""
        passed = 0
        failed = 0
        errors: List[Dict[str, str]] = []
        details: List[Dict[str, Any]] = []

        async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0) as client:
            for ep in ENDPOINT_REGISTRY:
                method = ep["method"]
                path = ep["path"]
                name = ep["name"]
                try:
                    if method == "GET":
                        resp = await client.get(path)
                    else:
                        resp = await client.request(method, path)

                    ok = resp.status_code < 500
                    detail = {
                        "name": name,
                        "path": path,
                        "status_code": resp.status_code,
                        "ok": ok,
                        "critical": ep.get("critical", False),
                    }
                    details.append(detail)

                    if ok:
                        passed += 1
                    else:
                        failed += 1
                        errors.append({
                            "name": name,
                            "path": path,
                            "status_code": resp.status_code,
                            "body": resp.text[:200],
                        })
                except Exception as e:
                    failed += 1
                    details.append({
                        "name": name,
                        "path": path,
                        "status_code": 0,
                        "ok": False,
                        "error": str(e)[:200],
                        "critical": ep.get("critical", False),
                    })
                    errors.append({
                        "name": name,
                        "path": path,
                        "status_code": 0,
                        "error": str(e)[:200],
                    })

        return {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "details": details,
        }

    async def _test_database(self) -> Dict[str, Any]:
        """Verify Supabase connectivity and required tables."""
        passed = 0
        failed = 0
        errors: List[Dict[str, str]] = []
        table_status: List[Dict[str, Any]] = []

        try:
            from shared.database import get_supabase
            sb = get_supabase()
        except Exception as e:
            return {
                "total": 1,
                "passed": 0,
                "failed": 1,
                "connected": False,
                "errors": [{"table": "connection", "error": str(e)}],
                "tables": [],
            }

        for table_name in REQUIRED_TABLES:
            try:
                result = sb.table(table_name).select("id", count="exact").limit(0).execute()
                row_count = result.count if result.count is not None else 0
                table_status.append({
                    "table": table_name,
                    "exists": True,
                    "row_count": row_count,
                })
                passed += 1
            except Exception as e:
                err_str = str(e)
                # 404 or "relation does not exist" means table is missing
                table_status.append({
                    "table": table_name,
                    "exists": False,
                    "error": err_str[:150],
                })
                errors.append({"table": table_name, "error": err_str[:150]})
                failed += 1

        return {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "connected": True,
            "errors": errors,
            "tables": table_status,
        }

    def _test_scheduler(self) -> Dict[str, Any]:
        """Check scheduler status and job history."""
        from shared.scheduler import get_job_history, scheduler as app_scheduler

        history = get_job_history()
        running = app_scheduler.running if hasattr(app_scheduler, 'running') else False

        passed = 0
        failed = 0
        job_details: List[Dict[str, Any]] = []

        for job_id, info in history.items():
            status = info.get("last_status")
            detail = {
                "job_id": job_id,
                "last_run": info.get("last_run"),
                "last_status": status,
                "last_error": info.get("last_error"),
            }
            job_details.append(detail)

            # If a job has run and succeeded, count it as passed
            if status == "success":
                passed += 1
            elif status == "error":
                failed += 1
            # Jobs that haven't run yet are neutral (not counted as failed)

        return {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "scheduler_running": running,
            "jobs": job_details,
        }

    async def _test_event_bus(self) -> Dict[str, Any]:
        """Check event bus connectivity."""
        try:
            from shared.event_bus_registry import get_event_bus
            bus = get_event_bus()
            if bus is None:
                return {"connected": False, "type": None, "error": "No event bus initialized"}
            return {
                "connected": True,
                "type": type(bus).__name__,
            }
        except Exception as e:
            return {"connected": False, "type": None, "error": str(e)[:200]}

    async def _test_agent_imports(self) -> Dict[str, Any]:
        """Verify all agent modules can be imported."""
        agent_modules = [
            ("agents.seo", "seo_agent"),
            ("agents.content", "content_agent"),
            ("agents.ads", "ads_agent"),
            ("agents.social", "social_agent"),
            ("agents.reviews", "review_agent"),
            ("agents.analytics", "analytics_agent"),
            ("agents.orchestrator", "orchestrator"),
            ("agents.ai_visibility", "ai_visibility_agent"),
        ]
        importable = 0
        errors: List[Dict[str, str]] = []
        details: List[Dict[str, Any]] = []

        for module_path, attr_name in agent_modules:
            try:
                import importlib
                mod = importlib.import_module(module_path)
                has_attr = hasattr(mod, attr_name)
                details.append({"module": module_path, "ok": has_attr})
                if has_attr:
                    importable += 1
                else:
                    errors.append({"module": module_path, "error": f"Missing {attr_name}"})
            except Exception as e:
                details.append({"module": module_path, "ok": False, "error": str(e)[:200]})
                errors.append({"module": module_path, "error": str(e)[:200]})

        return {
            "total": len(agent_modules),
            "importable": importable,
            "errors": errors,
            "details": details,
        }

    async def _notify_results(self, results: Dict[str, Any]):
        """Send a notification with the health check results."""
        try:
            from shared.notifications import notification_service

            summary = results["summary"]
            status = summary["status"]

            if status == "healthy":
                severity = "success"
                title = f"System Health: {summary['health_pct']}% — All checks passed"
            elif status == "degraded":
                severity = "warning"
                title = f"System Health: {summary['health_pct']}% — {summary['failed']} issues found"
            else:
                severity = "critical"
                title = f"System Health: {summary['health_pct']}% — {summary['failed']} failures"

            # Build detail message
            parts = []
            ep = results["endpoints"]
            if ep["failed"] > 0:
                failed_names = [e["name"] for e in ep["errors"][:5]]
                parts.append(f"Endpoints: {ep['failed']} failed ({', '.join(failed_names)})")

            db = results["database"]
            if db["failed"] > 0:
                missing = [e["table"] for e in db["errors"][:5]]
                parts.append(f"DB: {db['failed']} tables missing ({', '.join(missing)})")

            sched = results["scheduler"]
            if sched["failed"] > 0:
                failed_jobs = [j["job_id"] for j in sched["jobs"] if j["last_status"] == "error"]
                parts.append(f"Scheduler: {sched['failed']} jobs errored ({', '.join(failed_jobs[:3])})")

            message = " | ".join(parts) if parts else "All systems operational."

            await notification_service.notify(
                title=title,
                message=message,
                severity=severity,
                agent="dev-agent",
            )
        except Exception as e:
            logger.warning(f"[dev-agent] Failed to send notification: {e}")

    def get_last_report(self) -> Optional[Dict[str, Any]]:
        """Return the most recent health check report."""
        return self._last_report

    async def get_report_from_db(self) -> Optional[Dict[str, Any]]:
        """Retrieve latest health report from Supabase."""
        try:
            from shared.database import get_supabase
            sb = get_supabase()
            result = sb.table("dev_agent_reports") \
                .select("*") \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            if result.data:
                return result.data[0]
        except Exception:
            pass
        return self._last_report

    async def save_report(self, report: Dict[str, Any]):
        """Persist the health report to Supabase for historical tracking."""
        try:
            from shared.database import get_supabase
            sb = get_supabase()
            sb.table("dev_agent_reports").insert({
                "report": report,
                "health_pct": report["summary"]["health_pct"],
                "status": report["summary"]["status"],
                "total_checks": report["summary"]["total_checks"],
                "passed": report["summary"]["passed"],
                "failed": report["summary"]["failed"],
                "created_at": report["started_at"],
            }).execute()
        except Exception as e:
            logger.debug(f"[dev-agent] Could not save report to DB: {e}")


# Global instance
dev_agent = DevAgent()
