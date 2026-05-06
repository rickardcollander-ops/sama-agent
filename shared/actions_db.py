"""
Database helper for storing and retrieving agent actions.
Integrates with the autonomy framework for auto-execution
and the event bus for inter-agent collaboration chains.
"""

from typing import Dict, Any, List, Optional
from shared.database import get_supabase
import logging

logger = logging.getLogger(__name__)


async def _classify_and_handle(agent_name: str, action: Dict[str, Any], db_id: str):
    """Classify action tier and auto-execute or notify if applicable."""
    try:
        from shared.autonomy import autonomy, DecisionTier
        tier = autonomy.classify(action)

        if tier == DecisionTier.AUTO_EXECUTE:
            logger.info(f"[autonomy] Auto-executing: {action.get('title', '')[:50]}")
            sb = get_supabase()
            sb.table("agent_actions").update({
                "status": "auto_executed",
                "execution_result": {"auto": True, "tier": tier.value},
            }).eq("id", db_id).execute()

        elif tier == DecisionTier.AUTO_EXECUTE_NOTIFY:
            logger.info(f"[autonomy] Auto-execute+notify: {action.get('title', '')[:50]}")
            sb = get_supabase()
            sb.table("agent_actions").update({
                "status": "auto_executed",
                "execution_result": {"auto": True, "tier": tier.value},
            }).eq("id", db_id).execute()
            try:
                from shared.notifications import notification_service
                await notification_service.notify(
                    title=f"[{agent_name}] Auto-executed",
                    message=action.get("title", ""),
                    severity="info",
                    agent=agent_name,
                )
            except Exception:
                pass

        # For REQUIRE_APPROVAL: action stays as "pending" (default)
    except Exception as e:
        logger.debug(f"[autonomy] Classification skipped: {e}")


async def _publish_chain_events(agent_name: str, action: Dict[str, Any]):
    """Publish events to trigger downstream agent chains."""
    try:
        from shared.event_bus_registry import get_event_bus
        from shared.agent_chains import EVENT_KEYWORD_GAP_FOUND, EVENT_RANKING_DECLINE

        bus = get_event_bus()
        if not bus:
            return

        action_type = action.get("type", "")

        if agent_name == "seo" and action_type in ("content_gap", "content_creation"):
            await bus.publish(
                EVENT_KEYWORD_GAP_FOUND,
                "sama_content",
                {"keyword": action.get("keyword", ""), "priority": action.get("priority", "medium"), "gap_type": "blog_post"},
            )
        elif agent_name == "seo" and action_type == "content_refresh":
            await bus.publish(
                EVENT_RANKING_DECLINE,
                "sama_content",
                {"keyword": action.get("keyword", ""), "url": action.get("target_page", "")},
            )
    except Exception as e:
        logger.debug(f"[chains] Event publish skipped: {e}")


async def clear_pending_actions(agent_name: str, tenant_id: str = "default") -> int:
    """Delete all pending actions for an agent+tenant and return deleted count."""
    sb = get_supabase()

    try:
        result = (
            sb.table("agent_actions")
            .delete()
            .eq("agent_name", agent_name)
            .eq("tenant_id", tenant_id)
            .eq("status", "pending")
            .execute()
        )
        deleted_count = len(result.data or [])
        logger.info(f"ð§¹ Cleared {deleted_count} pending {agent_name} actions for tenant {tenant_id}")
        return deleted_count
    except Exception as e:
        logger.error(f"❌ Failed to clear pending actions for {agent_name}: {e}")
        return 0


async def save_actions(
    agent_name: str,
    actions: List[Dict[str, Any]],
    tenant_id: str = "default",
) -> List[str]:
    """
    Save actions to database with deduplication.

    Args:
        agent_name: Name of the agent (seo, ads, content, social, reviews)
        actions: List of action dicts from /analyze
        tenant_id: Tenant this action belongs to

    Returns:
        List of created action IDs
    """
    sb = get_supabase()
    created_ids = []

    # Replace queue on each analysis run — scoped to this tenant only.
    await clear_pending_actions(agent_name, tenant_id)

    for action in actions:
        try:
            action_id = action.get("id", "")

            db_action = {
                "tenant_id": tenant_id,
                "agent_name": agent_name,
                "action_id": action_id,
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
                "status": "pending",
            }

            result = sb.table("agent_actions").insert(db_action).execute()

            if result.data:
                db_id = result.data[0]["id"]
                created_ids.append(db_id)
                logger.info(f"✅ Saved action: {action.get('title', '')[:50]}")

                await _classify_and_handle(agent_name, action, db_id)
                await _publish_chain_events(agent_name, action)

        except Exception as e:
            logger.error(f"❌ Failed to save action: {e}")
            continue

    return created_ids


async def get_actions(
    agent_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Get actions from database.

    Args:
        agent_name: Filter by agent name
        status: Filter by status (pending, executing, completed, failed)
        limit: Max number of actions to return
        tenant_id: Restrict to this tenant (required for multi-tenant safety)

    Returns:
        List of actions
    """
    sb = get_supabase()

    try:
        query = sb.table("agent_actions").select("*")

        if tenant_id:
            query = query.eq("tenant_id", tenant_id)

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
    error_message: Optional[str] = None,
) -> bool:
    """
    Update action status and execution result.

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
    Get a single action by its action_id (not UUID).

    Args:
        action_id: The action_id field (e.g., 'ads-cpa-campaign-name')

    Returns:
        Action dict or None
    """
    sb = get_supabase()

    try:
        result = (
            sb.table("agent_actions")
            .select("*")
            .eq("action_id", action_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

        return None

    except Exception as e:
        logger.error(f"❌ Failed to get action: {e}")
        return None


async def delete_action(action_uuid: str) -> bool:
    """
    Delete an action from the database by its UUID.

    Args:
        action_uuid: UUID of the action (the 'id' column)

    Returns:
        True if successful
    """
    sb = get_supabase()

    try:
        sb.table("agent_actions").delete().eq("id", action_uuid).execute()
        logger.info(f"ð§ Deleted action {action_uuid}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to delete action: {e}")
        return False


async def get_pending_actions(
    agent_name: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get all pending actions, optionally filtered by agent and tenant."""
    return await get_actions(agent_name=agent_name, status="pending", tenant_id=tenant_id)


async def get_completed_actions(
    agent_name: Optional[str] = None,
    limit: int = 50,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get completed actions, optionally filtered by agent and tenant."""
    return await get_actions(agent_name=agent_name, status="completed", limit=limit, tenant_id=tenant_id)
