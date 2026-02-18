"""
OODA Loop Helper for Autonomous Agents
Provides utilities for tracking Observe → Orient → Decide → Act → Reflect cycles
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from uuid import uuid4

from shared.database import get_supabase

logger = logging.getLogger(__name__)


class OODALoop:
    """
    OODA Loop tracker for agent cycles
    
    Usage:
        ooda = OODALoop(agent_name="seo")
        await ooda.start_cycle()
        
        # OBSERVE
        observations = await ooda.observe(fetch_data_function)
        
        # ORIENT
        analysis = await ooda.orient(analyze_function, observations)
        
        # DECIDE
        decisions = await ooda.decide(decide_function, analysis)
        
        # ACT (called from /execute endpoint)
        await ooda.record_action(action_id, result)
        
        # REFLECT
        await ooda.reflect(reflection_function, decisions, actions_taken)
    """
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.cycle_id: Optional[str] = None
        self.cycle_number: int = 0
        self.sb = get_supabase()
    
    async def start_cycle(self) -> str:
        """Start a new OODA cycle"""
        # Get next cycle number
        result = self.sb.table("agent_cycles").select("cycle_number").eq("agent_name", self.agent_name).order("cycle_number", desc=True).limit(1).execute()
        
        if result.data:
            self.cycle_number = result.data[0]["cycle_number"] + 1
        else:
            self.cycle_number = 1
        
        # Create new cycle record
        cycle = self.sb.table("agent_cycles").insert({
            "agent_name": self.agent_name,
            "cycle_number": self.cycle_number,
            "status": "observing",
            "observe_started_at": datetime.utcnow().isoformat()
        }).execute()
        
        self.cycle_id = cycle.data[0]["id"]
        logger.info(f"🔄 {self.agent_name} Agent: Started OODA cycle #{self.cycle_number} (ID: {self.cycle_id})")
        
        return self.cycle_id
    
    async def observe(self, observations: Dict[str, Any]) -> Dict[str, Any]:
        """Record OBSERVE phase - data fetched from external sources"""
        if not self.cycle_id:
            raise ValueError("Must call start_cycle() first")
        
        self.sb.table("agent_cycles").update({
            "observations": observations,
            "observe_completed_at": datetime.utcnow().isoformat(),
            "status": "orienting",
            "orient_started_at": datetime.utcnow().isoformat()
        }).eq("id", self.cycle_id).execute()
        
        logger.info(f"👁️ {self.agent_name}: OBSERVE complete - {len(observations)} data sources")
        return observations
    
    async def orient(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Record ORIENT phase - analysis and insights"""
        if not self.cycle_id:
            raise ValueError("Must call start_cycle() first")
        
        self.sb.table("agent_cycles").update({
            "analysis": analysis,
            "orient_completed_at": datetime.utcnow().isoformat(),
            "status": "deciding",
            "decide_started_at": datetime.utcnow().isoformat()
        }).eq("id", self.cycle_id).execute()
        
        logger.info(f"🧠 {self.agent_name}: ORIENT complete - {analysis.get('insights_count', 0)} insights")
        return analysis
    
    async def decide(self, decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Record DECIDE phase - actions to take"""
        if not self.cycle_id:
            raise ValueError("Must call start_cycle() first")
        
        self.sb.table("agent_cycles").update({
            "decisions": {"actions": decisions, "count": len(decisions)},
            "decide_completed_at": datetime.utcnow().isoformat(),
            "status": "acting",
            "act_started_at": datetime.utcnow().isoformat()
        }).eq("id", self.cycle_id).execute()
        
        logger.info(f"🎯 {self.agent_name}: DECIDE complete - {len(decisions)} actions planned")
        return decisions
    
    async def record_action(self, action_id: str, action_data: Dict[str, Any], result: Dict[str, Any]):
        """Record an executed action during ACT phase"""
        if not self.cycle_id:
            logger.warning(f"No active cycle for {self.agent_name}, cannot record action")
            return
        
        # Get current actions_taken
        cycle = self.sb.table("agent_cycles").select("actions_taken").eq("id", self.cycle_id).execute()
        actions_taken = cycle.data[0].get("actions_taken", {}) if cycle.data else {}
        
        if not actions_taken:
            actions_taken = {"actions": []}
        
        # Add this action
        actions_taken["actions"].append({
            "action_id": action_id,
            "action_data": action_data,
            "result": result,
            "executed_at": datetime.utcnow().isoformat()
        })
        
        # Update cycle
        self.sb.table("agent_cycles").update({
            "actions_taken": actions_taken
        }).eq("id", self.cycle_id).execute()
        
        logger.info(f"⚡ {self.agent_name}: ACT - Recorded action {action_id}")
    
    async def complete_act_phase(self):
        """Mark ACT phase as complete and move to REFLECT"""
        if not self.cycle_id:
            return
        
        self.sb.table("agent_cycles").update({
            "act_completed_at": datetime.utcnow().isoformat(),
            "status": "reflecting",
            "reflect_started_at": datetime.utcnow().isoformat()
        }).eq("id", self.cycle_id).execute()
        
        logger.info(f"✅ {self.agent_name}: ACT phase complete")
    
    async def reflect(self, reflection: Dict[str, Any], learnings: Optional[List[Dict[str, Any]]] = None):
        """Record REFLECT phase - evaluate outcomes and learn"""
        if not self.cycle_id:
            raise ValueError("Must call start_cycle() first")
        
        self.sb.table("agent_cycles").update({
            "reflection": reflection,
            "reflect_completed_at": datetime.utcnow().isoformat(),
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", self.cycle_id).execute()
        
        # Store learnings
        if learnings:
            for learning in learnings:
                self.sb.table("agent_learnings").insert({
                    "agent_name": self.agent_name,
                    "cycle_id": self.cycle_id,
                    "learning_type": learning.get("type", "insight"),
                    "context": learning.get("context", {}),
                    "action_taken": learning.get("action_taken"),
                    "expected_outcome": learning.get("expected_outcome"),
                    "actual_outcome": learning.get("actual_outcome"),
                    "confidence_score": learning.get("confidence", 0.5)
                }).execute()
        
        logger.info(f"🔍 {self.agent_name}: REFLECT complete - {len(learnings or [])} learnings stored")
        logger.info(f"🎉 {self.agent_name}: OODA cycle #{self.cycle_number} COMPLETE")
    
    async def fail_cycle(self, error_message: str):
        """Mark cycle as failed"""
        if not self.cycle_id:
            return
        
        self.sb.table("agent_cycles").update({
            "status": "failed",
            "error_message": error_message,
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", self.cycle_id).execute()
        
        logger.error(f"❌ {self.agent_name}: OODA cycle #{self.cycle_number} FAILED - {error_message}")
    
    async def get_recent_cycles(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent OODA cycles for this agent"""
        result = self.sb.table("agent_cycles").select("*").eq("agent_name", self.agent_name).order("created_at", desc=True).limit(limit).execute()
        return result.data or []
    
    async def get_learnings(self, limit: int = 50, learning_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get agent learnings"""
        query = self.sb.table("agent_learnings").select("*").eq("agent_name", self.agent_name)
        
        if learning_type:
            query = query.eq("learning_type", learning_type)
        
        result = query.order("created_at", desc=True).limit(limit).execute()
        return result.data or []
    
    async def get_current_cycle_status(self) -> Optional[Dict[str, Any]]:
        """Get status of current active cycle"""
        result = self.sb.table("agent_cycles").select("*").eq("agent_name", self.agent_name).in_("status", ["observing", "orienting", "deciding", "acting", "reflecting"]).order("created_at", desc=True).limit(1).execute()
        
        if result.data:
            return result.data[0]
        return None


async def get_agent_stats(agent_name: str) -> Dict[str, Any]:
    """Get overall stats for an agent"""
    sb = get_supabase()
    
    # Total cycles
    cycles = sb.table("agent_cycles").select("id, status").eq("agent_name", agent_name).execute()
    total_cycles = len(cycles.data or [])
    completed = sum(1 for c in cycles.data if c["status"] == "completed")
    failed = sum(1 for c in cycles.data if c["status"] == "failed")
    
    # Learnings
    learnings = sb.table("agent_learnings").select("learning_type").eq("agent_name", agent_name).execute()
    total_learnings = len(learnings.data or [])
    
    learning_breakdown = {}
    for l in (learnings.data or []):
        lt = l["learning_type"]
        learning_breakdown[lt] = learning_breakdown.get(lt, 0) + 1
    
    return {
        "agent_name": agent_name,
        "total_cycles": total_cycles,
        "completed_cycles": completed,
        "failed_cycles": failed,
        "success_rate": round(completed / total_cycles * 100, 1) if total_cycles > 0 else 0,
        "total_learnings": total_learnings,
        "learning_breakdown": learning_breakdown
    }
