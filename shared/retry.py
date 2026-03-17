"""
Retry and Circuit Breaker utilities for external API calls.
Provides decorators for automatic retry with exponential backoff
and circuit breaker pattern to avoid hammering failing services.
"""

import asyncio
import functools
import logging
import time
from typing import Optional, Tuple, Type

logger = logging.getLogger(__name__)

# ── Circuit Breaker ──────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Simple circuit breaker: after `failure_threshold` consecutive failures,
    opens the circuit for `recovery_timeout` seconds.
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at >= self.recovery_timeout:
            # Half-open: allow one attempt
            return False
        return True

    def record_success(self):
        self.failures = 0
        self.opened_at = None

    def record_failure(self):
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()
            logger.warning(
                f"Circuit breaker OPEN after {self.failures} failures "
                f"(recovery in {self.recovery_timeout}s)"
            )


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


# Global circuit breakers keyed by service name
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(service: str, **kwargs) -> CircuitBreaker:
    if service not in _breakers:
        _breakers[service] = CircuitBreaker(**kwargs)
    return _breakers[service]


# ── Retry Decorator ──────────────────────────────────────────────────────────

def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    circuit_breaker_service: Optional[str] = None,
):
    """
    Async retry decorator with exponential backoff and optional circuit breaker.

    Usage:
        @with_retry(max_attempts=3, circuit_breaker_service="google_ads")
        async def call_google_ads_api():
            ...
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Check circuit breaker
            breaker = None
            if circuit_breaker_service:
                breaker = get_breaker(circuit_breaker_service)
                if breaker.is_open:
                    raise CircuitOpenError(
                        f"Circuit breaker open for {circuit_breaker_service}"
                    )

            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)
                    if breaker:
                        breaker.record_success()
                    return result
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = min(
                            base_delay * (backoff_factor ** (attempt - 1)),
                            max_delay,
                        )
                        logger.warning(
                            f"[retry] {func.__name__} attempt {attempt}/{max_attempts} "
                            f"failed: {e}. Retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"[retry] {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        if breaker:
                            breaker.record_failure()

            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
