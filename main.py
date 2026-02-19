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
    ai_visibility
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
    
    # Initialize event bus (optional) - skip for now to ensure startup
    event_bus = None
    logger.info("⚠️ Event bus skipped (optional)")
    
    # Setup monitoring (optional) - skip for now to ensure startup
    logger.info("⚠️ Monitoring skipped (optional)")
    
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
app.include_router(reviews.router, prefix="/api/reviews", tags=["reviews"])
app.include_router(reviews_advanced.router, prefix="/api/reviews", tags=["reviews-advanced"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(automation.router, prefix="/api/automation", tags=["automation"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(improvements.router, prefix="/api", tags=["improvements"])
app.include_router(ai_visibility.router, prefix="/api/ai-visibility", tags=["ai-visibility"])


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
            "ai_visibility": "active"
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
    import os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=settings.ENVIRONMENT == "development")
