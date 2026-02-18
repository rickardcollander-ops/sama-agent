"""
Database helper for storing and retrieving chat history
"""

from typing import List, Dict, Any
from shared.database import get_supabase
import logging

logger = logging.getLogger(__name__)


async def save_message(agent_name: str, role: str, message: str, user_id: str = "default_user") -> bool:
    """
    Save a chat message to database
    
    Args:
        agent_name: Name of the agent (content, seo, ads, social, reviews)
        role: 'user' or 'agent'
        message: The message content
        user_id: User identifier (default: 'default_user')
    
    Returns:
        True if successful
    """
    sb = get_supabase()
    
    try:
        sb.table("chat_history").insert({
            "agent_name": agent_name,
            "user_id": user_id,
            "role": role,
            "message": message
        }).execute()
        
        logger.info(f"💬 Saved {role} message for {agent_name}")
        return True
    
    except Exception as e:
        logger.error(f"❌ Failed to save chat message: {e}")
        return False


async def get_chat_history(agent_name: str, user_id: str = "default_user", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Get chat history for an agent and user
    
    Args:
        agent_name: Name of the agent
        user_id: User identifier
        limit: Max number of messages to return
    
    Returns:
        List of messages in chronological order
    """
    sb = get_supabase()
    
    try:
        result = sb.table("chat_history").select("*").eq("agent_name", agent_name).eq("user_id", user_id).order("created_at", desc=False).limit(limit).execute()
        
        return result.data or []
    
    except Exception as e:
        logger.error(f"❌ Failed to get chat history: {e}")
        return []


async def clear_chat_history(agent_name: str, user_id: str = "default_user") -> bool:
    """
    Clear chat history for an agent and user
    
    Args:
        agent_name: Name of the agent
        user_id: User identifier
    
    Returns:
        True if successful
    """
    sb = get_supabase()
    
    try:
        sb.table("chat_history").delete().eq("agent_name", agent_name).eq("user_id", user_id).execute()
        
        logger.info(f"🗑️ Cleared chat history for {agent_name}")
        return True
    
    except Exception as e:
        logger.error(f"❌ Failed to clear chat history: {e}")
        return False
