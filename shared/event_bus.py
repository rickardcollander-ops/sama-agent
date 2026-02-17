"""
Event Bus for inter-agent communication
Uses Redis Streams for reliable message passing between SAMA and LinkedIn Agent
"""

import json
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import redis.asyncio as redis

from .config import settings

logger = logging.getLogger(__name__)


class EventBus:
    """Redis Streams-based event bus for agent communication"""
    
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.stream_name = "sama:events"
        self.consumer_group = "sama_agent"
        self.consumer_name = "sama_worker_1"
        self.handlers: Dict[str, Callable] = {}
    
    async def connect(self):
        """Connect to Redis"""
        self.redis_client = await redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        
        # Create consumer group if it doesn't exist
        try:
            await self.redis_client.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id="0",
                mkstream=True
            )
            logger.info(f"✅ Created consumer group: {self.consumer_group}")
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"Consumer group {self.consumer_group} already exists")
            else:
                raise
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Disconnected from Redis")
    
    async def publish(
        self,
        event_type: str,
        target_agent: str,
        data: Dict[str, Any]
    ):
        """
        Publish an event to the event bus
        
        Args:
            event_type: Type of event (e.g., "content_published", "keyword_discovered")
            target_agent: Target agent (e.g., "linkedin_agent", "sama_seo")
            data: Event payload
        """
        event = {
            "event_type": event_type,
            "source_agent": "sama",
            "target_agent": target_agent,
            "timestamp": datetime.utcnow().isoformat(),
            "data": json.dumps(data)
        }
        
        message_id = await self.redis_client.xadd(self.stream_name, event)
        logger.info(f"📤 Published event: {event_type} → {target_agent} (ID: {message_id})")
        
        return message_id
    
    async def subscribe(self, event_type: str, handler: Callable):
        """
        Register a handler for a specific event type
        
        Args:
            event_type: Event type to listen for
            handler: Async function to handle the event
        """
        self.handlers[event_type] = handler
        logger.info(f"📥 Subscribed to event type: {event_type}")
    
    async def consume(self, count: int = 10, block: int = 5000):
        """
        Consume events from the stream
        
        Args:
            count: Number of messages to fetch
            block: Block for this many milliseconds if no messages
        """
        if not self.redis_client:
            raise RuntimeError("Event bus not connected")
        
        # Read from consumer group
        messages = await self.redis_client.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {self.stream_name: ">"},
            count=count,
            block=block
        )
        
        for stream_name, stream_messages in messages:
            for message_id, message_data in stream_messages:
                await self._process_message(message_id, message_data)
    
    async def _process_message(self, message_id: str, message_data: Dict[str, str]):
        """Process a single message"""
        try:
            event_type = message_data.get("event_type")
            target_agent = message_data.get("target_agent")
            
            # Only process if targeted at SAMA
            if target_agent not in ["sama", "sama_seo", "sama_content", "sama_ads", "sama_social", "sama_reviews", "sama_analytics"]:
                await self.redis_client.xack(self.stream_name, self.consumer_group, message_id)
                return
            
            # Parse data
            data = json.loads(message_data.get("data", "{}"))
            
            # Call handler if registered
            if event_type in self.handlers:
                logger.info(f"📨 Processing event: {event_type} from {message_data.get('source_agent')}")
                await self.handlers[event_type](data)
            else:
                logger.warning(f"⚠️ No handler for event type: {event_type}")
            
            # Acknowledge message
            await self.redis_client.xack(self.stream_name, self.consumer_group, message_id)
            
        except Exception as e:
            logger.error(f"❌ Error processing message {message_id}: {e}")
            # Don't ack - message will be retried


# Global event bus instance
event_bus = EventBus()
