"""
Dev Agent API — system health checks and diagnostics
"""

from fastapi import APIRouter
import logging

from agents.dev_agent import dev_agent

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health-check")
async def run_health_check():
    """Run a full system health check and return the report."""
    report = await dev_agent.run_full_health_check()
    await dev_agent.save_report(report)
    return report


@router.get("/health-check/latest")
async def get_latest_report():
    """Get the most recent health check report (from memory or DB)."""
    report = await dev_agent.get_report_from_db()
    if report:
        return report
    return {"message": "No health check has been run yet. Trigger one via POST /api/dev-agent/health-check."}


@router.get("/health-check/endpoints")
async def test_endpoints_only():
    """Test only API endpoints (faster than full check)."""
    return await dev_agent._test_endpoints()


@router.get("/health-check/database")
async def test_database_only():
    """Test only database tables."""
    return await dev_agent._test_database()


@router.get("/health-check/scheduler")
async def test_scheduler_only():
    """Test only scheduler status."""
    return dev_agent._test_scheduler()
