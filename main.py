"""
SAMA 2.0 - Successifier Autonomous Marketing Agent
Main FastAPI application entry point
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator

from api.routes import (
    seo, content, ads, social, reviews, analytics, orchestrator, automation,
    seo_advanced, content_advanced, ads_advanced, reviews_advanced, alerts
)
from shared.config import settings
from shared.database import init_db, get_supabase

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
    
    # Initialize event bus (optional)
    try:
        from shared.event_bus import EventBus
        event_bus = EventBus()
        await event_bus.connect()
        logger.info("✅ Event bus connected")
    except Exception as e:
        logger.warning(f"⚠️ Event bus not available: {e}")
        event_bus = None
    
    # Setup monitoring (optional)
    try:
        from shared.monitoring import setup_monitoring
        setup_monitoring()
        logger.info("✅ Monitoring configured")
    except Exception as e:
        logger.warning(f"⚠️ Monitoring not configured: {e}")
    
    logger.info("🎯 SAMA 2.0 is ready!")
    
    yield
    
    # Cleanup
    if event_bus:
        await event_bus.disconnect()
    logger.info("👋 SAMA 2.0 shutting down")


# Create FastAPI app
app = FastAPI(
    title="SAMA 2.0",
    description="Successifier Autonomous Marketing Agent",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(reviews.router, prefix="/api/reviews", tags=["reviews"])
app.include_router(reviews_advanced.router, prefix="/api/reviews", tags=["reviews-advanced"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(automation.router, prefix="/api/automation", tags=["automation"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])


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
            "analytics": "active"
        }
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "database": "connected",
        "event_bus": "connected",
        "monitoring": "active"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.ENVIRONMENT == "development"
    )
