"""
Database helper for storing and retrieving agent actions
"""

from typing import Dict, Any, List, Optional
from shared.database import get_supabase
import logging

logger = logging.getLogger(__name__)


async def save_actions(agent_name: str, actions: List[Dict[str, Any]]) -> List[str]:
    """
    Save actions to database
    
    Args:
        agent_name: Name of the agent (seo, ads, content, social, reviews)
        actions: List of action dicts from /analyze
    
    Returns:
        List of created action IDs
    """
    sb = get_supabase()
    created_ids = []
    
    for action in actions:
        try:
            # Prepare action for database
            db_action = {
                "agent_name": agent_name,
                "action_id": action.get("id", ""),
                "action_type": action.get("type", ""),
                "priority": action.get("priority", "medium"),
                "title": action.get("title", ""),
                "description": action.get("description", ""),
                "action": action.get("action", ""),
                "keyword": action.get("keyword"),
                "content_id": action.get("content_id"),
                "competitor": action.get("competitor"),
                "campaign": action.get("campaign"),
                "platform": action.get("platform"),
                "target_page": action.get("target_page"),
                "expected_outcome": action.get("expected_outcome"),
                "status": "pending"
            }
            
            # Insert into database
            result = sb.table("agent_actions").insert(db_action).execute()
            
            if result.data:
                created_ids.append(result.data[0]["id"])
                logger.info(f"✅ Saved action: {action.get('title', '')[:50]}")
        
        except Exception as e:
            logger.error(f"❌ Failed to save action: {e}")
            continue
    
    return created_ids


async def get_actions(
    agent_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get actions from database
    
    Args:
        agent_name: Filter by agent name
        status: Filter by status (pending, executing, completed, failed)
        limit: Max number of actions to return
    
    Returns:
        List of actions
    """
    sb = get_supabase()
    
    try:
        query = sb.table("agent_actions").select("*")
        
        if agent_name:
            query = query.eq("agent_name", agent_name)
        
        if status:
            query = query.eq("status", status)
        
        result = query.order("created_at", desc=True).limit(limit).execute()
        
        return result.data or []
    
    except Exception as e:
        logger.error(f"❌ Failed to get actions: {e}")
        return []


async def update_action_status(
    action_id: str,
    status: str,
    execution_result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None
) -> bool:
    """
    Update action status and execution result
    
    Args:
        action_id: UUID of the action
        status: New status (executing, completed, failed)
        execution_result: Result data from execution
        error_message: Error message if failed
    
    Returns:
        True if successful
    """
    sb = get_supabase()
    
    try:
        update_data = {"status": status}
        
        if status == "executing":
            from datetime import datetime
            update_data["executed_at"] = datetime.utcnow().isoformat()
        
        if execution_result:
            update_data["execution_result"] = execution_result
        
        if error_message:
            update_data["error_message"] = error_message
        
        result = sb.table("agent_actions").update(update_data).eq("id", action_id).execute()
        
        logger.info(f"✅ Updated action {action_id} to status: {status}")
        return True
    
    except Exception as e:
        logger.error(f"❌ Failed to update action: {e}")
        return False


async def get_action_by_action_id(action_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a single action by its action_id (not UUID)
    
    Args:
        action_id: The action_id field (e.g., 'ads-cpa-campaign-name')
    
    Returns:
        Action dict or None
    """
    sb = get_supabase()
    
    try:
        result = sb.table("agent_actions").select("*").eq("action_id", action_id).order("created_at", desc=True).limit(1).execute()
        
        if result.data:
            return result.data[0]
        
        return None
    
    except Exception as e:
        logger.error(f"❌ Failed to get action: {e}")
        return None


async def get_pending_actions(agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all pending actions, optionally filtered by agent"""
    return await get_actions(agent_name=agent_name, status="pending")


async def get_completed_actions(agent_name: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Get completed actions, optionally filtered by agent"""
    return await get_actions(agent_name=agent_name, status="completed", limit=limit)
