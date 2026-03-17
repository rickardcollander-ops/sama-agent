"""
Agent Chat API — chat with individual agents, team discussions, or broadcast
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
import logging

from shared.agent_chat import (
    chat_with_agent, chat_with_all_agents, chat_with_team,
    list_agents, get_conversations, get_chat_messages,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


@router.get("/chat/agents")
async def get_available_agents():
    """List all agents with their names and personas."""
    return {"agents": list_agents()}


@router.get("/chat/conversations")
async def list_conversations(mode: Optional[str] = Query(None)):
    """List recent conversations. Filter by mode: 'team', agent key, or None for all."""
    convos = await get_conversations(mode)
    return {"conversations": convos}


@router.get("/chat/history/{conversation_id:path}")
async def get_history(conversation_id: str):
    """Get all messages for a conversation."""
    messages = await get_chat_messages(conversation_id)
    return {"conversation_id": conversation_id, "messages": messages}


@router.post("/chat/team")
async def team_chat(req: ChatRequest):
    """
    Intelligent team chat — routes the message to the most relevant 1-3 agents.
    Agents respond in sequence, each seeing what the previous ones said.
    """
    result = await chat_with_team(req.message, req.conversation_id)
    return result


@router.post("/chat/broadcast")
async def broadcast_message(req: ChatRequest):
    """Send a message to all agents and collect responses."""
    result = await chat_with_all_agents(req.message, req.conversation_id)
    return result


@router.post("/chat/{agent_name}")
async def send_chat_message(agent_name: str, req: ChatRequest):
    """Send a message to a specific agent."""
    result = await chat_with_agent(agent_name, req.message, req.conversation_id)
    return result
