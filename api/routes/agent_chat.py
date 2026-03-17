"""
Agent Chat API — chat with individual agents or broadcast to all
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import logging

from shared.agent_chat import chat_with_agent, chat_with_all_agents, list_agents

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


@router.get("/chat/agents")
async def get_available_agents():
    """List all agents with their names and personas."""
    return {"agents": list_agents()}


@router.post("/chat/{agent_name}")
async def send_chat_message(agent_name: str, req: ChatRequest):
    """Send a message to a specific agent."""
    result = await chat_with_agent(agent_name, req.message, req.conversation_id)
    return result


@router.post("/chat/broadcast")
async def broadcast_message(req: ChatRequest):
    """Send a message to all agents and collect responses."""
    result = await chat_with_all_agents(req.message, req.conversation_id)
    return result
