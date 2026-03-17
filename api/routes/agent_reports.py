"""
Agent Reports API — daily self-reports from each agent
"""

from fastapi import APIRouter
import logging

from shared.agent_report import (
    generate_all_reports, generate_agent_report,
    get_latest_reports, get_all_improvements,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/reports")
async def list_latest_reports():
    """Get the most recent report for each agent."""
    reports = await get_latest_reports()
    return {"reports": reports, "total": len(reports)}


@router.post("/reports/generate")
async def trigger_report_generation():
    """Generate fresh reports for all agents (on-demand)."""
    reports = await generate_all_reports()
    return {"reports": reports, "total": len(reports)}


@router.post("/reports/generate/{agent_name}")
async def trigger_single_report(agent_name: str):
    """Generate a report for a single agent."""
    report = await generate_agent_report(agent_name)
    return report


@router.get("/reports/improvements")
async def list_improvements():
    """Get all improvement suggestions from latest reports (consumed by dev agent)."""
    items = await get_all_improvements()
    return {"improvements": items, "total": len(items)}
