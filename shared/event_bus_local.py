"""
In-process event bus fallback when Redis is unavailable.
Uses asyncio.Queue for single-instance deployments.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


class LocalEventBus:
    """In-memory event bus using asyncio.Queue — fallback when Redis is unavailable."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self.handlers: Dict[str, Callable] = {}
        self._running = False
        self._consumer_task: Optional[asyncio.Task] = None

    async def connect(self):
        """No-op for local bus (already ready)."""
        logger.info("LocalEventBus ready (in-process)")

    async def disconnect(self):
        """Stop the consumer loop."""
        self._running = False
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("LocalEventBus stopped")

    async def publish(self, event_type: str, target_agent: str, data: Dict[str, Any]):
        event = {
            "event_type": event_type,
            "source_agent": "sama",
            "target_agent": target_agent,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data,
        }
        await self._queue.put(event)
        logger.info(f"[local-bus] Published {event_type} -> {target_agent}")
        return f"local-{datetime.utcnow().timestamp()}"

    async def subscribe(self, event_type: str, handler: Callable):
        self.handlers[event_type] = handler
        logger.info(f"[local-bus] Subscribed to {event_type}")

    async def start_consumer(self):
        """Start background consumer loop."""
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("[local-bus] Consumer loop started")

    async def _consume_loop(self):
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self._process(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[local-bus] Error processing event: {e}")

    async def _process(self, event: Dict[str, Any]):
        event_type = event.get("event_type")
        target = event.get("target_agent", "")

        valid_targets = {
            "sama", "sama_seo", "sama_content", "sama_ads",
            "sama_social", "sama_reviews", "sama_analytics",
        }
        if target not in valid_targets:
            return

        if event_type in self.handlers:
            logger.info(f"[local-bus] Processing {event_type} from {event.get('source_agent')}")
            try:
                await self.handlers[event_type](event.get("data", {}))
            except Exception as e:
                logger.error(f"[local-bus] Handler error for {event_type}: {e}")
        else:
            logger.warning(f"[local-bus] No handler for {event_type}")

    # Convenience: same consume() signature as the Redis bus
    async def consume(self, count: int = 10, block: int = 5000):
        """Process up to `count` queued events (non-blocking)."""
        processed = 0
        while processed < count and not self._queue.empty():
            event = self._queue.get_nowait()
            await self._process(event)
            processed += 1
