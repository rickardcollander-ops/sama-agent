"""
Monitoring and observability setup
Sentry for error tracking, structured logging
"""

import logging
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from .config import settings


def setup_monitoring():
    """Initialize monitoring services"""
    
    # Sentry error tracking
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=1.0 if settings.ENVIRONMENT == "development" else 0.1,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
        )
        logging.info("✅ Sentry monitoring initialized")
    
    # Datadog would be initialized here if needed
    # For now, we'll use structured logging
    
    logging.info("✅ Monitoring configured")
