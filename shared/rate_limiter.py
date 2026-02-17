"""
Rate Limiter for API calls
Prevents hitting external API limits and manages request throttling
"""

from typing import Dict, Optional
from datetime import datetime, timedelta
import asyncio
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Simple in-memory rate limiter
    For production, use Redis-based rate limiting
    """
    
    def __init__(self):
        self._limits: Dict[str, Dict] = {}
    
    async def check_limit(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> bool:
        """
        Check if request is within rate limit
        
        Args:
            key: Unique identifier (e.g., "google_ads_api", "anthropic_api")
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
            
        Returns:
            True if request is allowed, False if rate limited
        """
        now = datetime.utcnow()
        
        if key not in self._limits:
            self._limits[key] = {
                "requests": [],
                "max_requests": max_requests,
                "window_seconds": window_seconds
            }
        
        limit_data = self._limits[key]
        
        # Remove old requests outside the window
        cutoff = now - timedelta(seconds=window_seconds)
        limit_data["requests"] = [
            req_time for req_time in limit_data["requests"]
            if req_time > cutoff
        ]
        
        # Check if we're at the limit
        if len(limit_data["requests"]) >= max_requests:
            oldest_request = min(limit_data["requests"])
            wait_time = (oldest_request + timedelta(seconds=window_seconds) - now).total_seconds()
            logger.warning(
                f"Rate limit reached for {key}. "
                f"{len(limit_data['requests'])}/{max_requests} requests in {window_seconds}s. "
                f"Wait {wait_time:.1f}s"
            )
            return False
        
        # Add current request
        limit_data["requests"].append(now)
        return True
    
    async def wait_if_needed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
        max_wait: int = 60
    ) -> bool:
        """
        Wait until rate limit allows request
        
        Args:
            key: Unique identifier
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
            max_wait: Maximum seconds to wait
            
        Returns:
            True if request can proceed, False if max_wait exceeded
        """
        waited = 0
        while not await self.check_limit(key, max_requests, window_seconds):
            if waited >= max_wait:
                logger.error(f"Max wait time ({max_wait}s) exceeded for {key}")
                return False
            
            await asyncio.sleep(1)
            waited += 1
        
        return True


# Rate limit configurations for different APIs
RATE_LIMITS = {
    # Google APIs
    "google_search_console": {"max_requests": 1200, "window_seconds": 60},  # 1200/min
    "google_ads_api": {"max_requests": 15000, "window_seconds": 60},  # 15k/min
    "google_indexing_api": {"max_requests": 200, "window_seconds": 86400},  # 200/day
    "google_pagespeed": {"max_requests": 25000, "window_seconds": 86400},  # 25k/day
    
    # Anthropic
    "anthropic_api": {"max_requests": 50, "window_seconds": 60},  # 50/min (tier 1)
    
    # Web scraping (be conservative)
    "g2_scraping": {"max_requests": 10, "window_seconds": 60},  # 10/min
    "capterra_scraping": {"max_requests": 10, "window_seconds": 60},  # 10/min
    "trustpilot_scraping": {"max_requests": 10, "window_seconds": 60},  # 10/min
    
    # Twitter API
    "twitter_api": {"max_requests": 300, "window_seconds": 900},  # 300/15min
}


# Global rate limiter instance
rate_limiter = RateLimiter()


async def rate_limit(api_name: str) -> bool:
    """
    Convenience function to check rate limit
    
    Usage:
        if await rate_limit("google_ads_api"):
            # Make API call
        else:
            # Handle rate limit
    """
    if api_name not in RATE_LIMITS:
        logger.warning(f"No rate limit configured for {api_name}")
        return True
    
    config = RATE_LIMITS[api_name]
    return await rate_limiter.check_limit(
        api_name,
        config["max_requests"],
        config["window_seconds"]
    )
