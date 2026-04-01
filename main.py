"""
SAMA 2.0 - Successifier Autonomous Marketing Agent
Main FastAPI application entry point
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import traceback
from typing import AsyncGenerator

from api.routes import (
    seo, content, ads, social, reviews, analytics, orchestrator, automation,
    seo_advanced, content_advanced, ads_advanced, reviews_advanced, alerts, improvements,
    ai_visibility, dashboard, social_reddit, gtm, goals, notifications, dev_agent,
    agent_reports, agent_chat, user_settings, leads, webhooks,
    content_pieces, content_generate, social_posts, analytics_overview,
    ads_creatives, ads_credentials, google_oauth,
)
from shared.config import settings
from shared.database import init_db, get_supabase
from shared import scheduler as job_scheduler
from shared.event_bus_registry import set_event_bus, get_event_bus
from shared.tenant_middleware import TenantMiddleware

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan manager"""
    logger.info("🚀 Starting SAMA 2.0...")
    
    # Initialize Supabase connection
    try:
        get_supabase()
        logger.info("✅ Supabase connected")
    except Exception as e:
        logger.warning(f"⚠️ Supabase not configured: {e}")
    
    # Initialize event bus (Redis with local fallback)
    event_bus = None
    try:
        if settings.REDIS_URL and settings.REDIS_URL != "redis://localhost:6379/0":
            from shared.event_bus import EventBus
            event_bus = EventBus()
            await event_bus.connect()
            logger.info("✅ Event bus connected (Redis)")
        else:
            raise ConnectionError("No remote Redis configured")
    except Exception as e:
        logger.info(f"ℹ️ Redis unavailable ({e}), using local event bus")
        from shared.event_bus_local import LocalEventBus
        event_bus = LocalEventBus()
        await event_bus.connect()

    set_event_bus(event_bus)

    # Register inter-agent collaboration chains
    try:
        from shared.agent_chains import register_all_chains
        await register_all_chains(event_bus)
        logger.info("✅ Agent collaboration chains registered")
    except Exception as e:
        logger.warning(f"⚠️ Failed to register chains: {e}")

    # Start event bus consumer (for local bus)
    if hasattr(event_bus, "start_consumer"):
        await event_bus.start_consumer()

    # Start proactive agent monitor loop
    monitor = None
    try:
        from shared.agent_monitor import AgentMonitorLoop, register_default_watchers
        monitor = AgentMonitorLoop()
        register_default_watchers(monitor)
        await monitor.start()
        logger.info("✅ Agent monitor loop started")
    except Exception as e:
        logger.warning(f"⚠️ Monitor loop failed to start: {e}")

    # Start job scheduler
    try:
        job_scheduler.start()
        logger.info("✅ Scheduler started")
    except Exception as e:
        logger.warning(f"⚠️ Scheduler failed to start: {e}")

    logger.info("🎯 SAMA 2.0 is ready!")

    yield

    # Cleanup
    if monitor:
        await monitor.stop()
    if event_bus:
        await event_bus.disconnect()
    set_event_bus(None)
    job_scheduler.stop()
    logger.info("👋 SAMA 2.0 shutting down")


# Create FastAPI app
app = FastAPI(
    title="SAMA 2.0",
    description="Successifier Autonomous Marketing Agent",
    version="2.0.0",
    lifespan=lifespan
)

# Tenant middleware - extracts tenant_id from requests
app.add_middleware(TenantMiddleware)

# CORS middleware - allow all origins for now to fix deployment issues
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins temporarily
    allow_credentials=False,  # Must be False when allow_origins is "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler - ensures CORS headers are always sent even on crashes
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

# Include routers
app.include_router(orchestrator.router, prefix="/api/orchestrator", tags=["orchestrator"])
app.include_router(seo.router, prefix="/api/seo", tags=["seo"])
app.include_router(seo_advanced.router, prefix="/api/seo", tags=["seo-advanced"])
app.include_router(content.router, prefix="/api/content", tags=["content"])
app.include_router(content_advanced.router, prefix="/api/content", tags=["content-advanced"])
app.include_router(ads.router, prefix="/api/ads", tags=["ads"])
app.include_router(ads_advanced.router, prefix="/api/ads", tags=["ads-advanced"])
app.include_router(social.router, prefix="/api/social", tags=["social"])
app.include_router(social_reddit.router, prefix="/api/social/reddit", tags=["social-reddit"])
app.include_router(reviews.router, prefix="/api/reviews", tags=["reviews"])
app.include_router(reviews_advanced.router, prefix="/api/reviews", tags=["reviews-advanced"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(automation.router, prefix="/api/automation", tags=["automation"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(improvements.router, prefix="/api", tags=["improvements"])
app.include_router(ai_visibility.router, prefix="/api/ai-visibility", tags=["ai-visibility"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(gtm.router, prefix="/api/gtm", tags=["gtm"])
app.include_router(goals.router, prefix="/api", tags=["goals"])
app.include_router(notifications.router, prefix="/api", tags=["notifications"])
app.include_router(dev_agent.router, prefix="/api/dev-agent", tags=["dev-agent"])
app.include_router(agent_reports.router, prefix="/api/agents", tags=["agent-reports"])
app.include_router(agent_chat.router, prefix="/api/agents", tags=["agent-chat"])
app.include_router(user_settings.router, prefix="/api", tags=["user-settings"])
app.include_router(leads.router, prefix="/api", tags=["leads"])
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(content_pieces.router, prefix="/api/content", tags=["content-pieces"])
app.include_router(content_generate.router, prefix="/api/content", tags=["content-generate"])
app.include_router(social_posts.router, prefix="/api/social", tags=["social-posts"])
app.include_router(analytics_overview.router, prefix="/api/analytics", tags=["analytics-overview"])
app.include_router(ads_creatives.router, prefix="/api/ads", tags=["ads-creatives"])
app.include_router(ads_credentials.router, prefix="/api/ads", tags=["ads-credentials"])
app.include_router(google_oauth.router, prefix="/api/auth/google", tags=["google-oauth"])


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "SAMA 2.0",
        "status": "operational",
        "version": "2.0.0",
        "agents": {
            "seo": "active",
            "content": "active",
            "ads": "active",
            "social": "active",
            "reviews": "active",
            "analytics": "active",
            "gtm": "active"
        }
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
    bus = get_event_bus()
    return {
        "status": "healthy",
        "database": "connected",
        "event_bus": type(bus).__name__ if bus else "disconnected",
        "monitoring": "active",
    }


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=settings.ENVIRONMENT == "development")
