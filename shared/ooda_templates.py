"""
OODA Loop Templates for Quick Agent Implementation
Provides reusable patterns for implementing OODA in all agents
"""

from typing import Dict, Any, List, Callable
from shared.ooda_loop import OODALoop


async def run_agent_ooda_cycle(
    agent_name: str,
    observe_fn: Callable,
    orient_fn: Callable,
    decide_fn: Callable
) -> Dict[str, Any]:
    """
    Generic OODA cycle runner for any agent
    
    Args:
        agent_name: Name of the agent (seo, ads, content, social, reviews)
        observe_fn: Async function that returns observations dict
        orient_fn: Async function that takes observations and returns analysis dict
        decide_fn: Async function that takes analysis and returns list of actions
    
    Returns:
        Full OODA cycle results with cycle_id, observations, analysis, actions
    """
    ooda = OODALoop(agent_name=agent_name)
    
    try:
        # Start cycle
        cycle_id = await ooda.start_cycle()
        
        # OBSERVE
        observations = await observe_fn()
        await ooda.observe(observations)
        
        # ORIENT
        analysis = await orient_fn(observations)
        await ooda.orient(analysis)
        
        # DECIDE
        actions = await decide_fn(analysis, observations)
        await ooda.decide(actions)
        
        # Return results (ACT happens via /execute, REFLECT happens after actions complete)
        return {
            "success": True,
            "cycle_id": cycle_id,
            "cycle_number": ooda.cycle_number,
            "ooda_status": "decided",
            "summary": {
                "total_actions": len(actions),
                "critical": sum(1 for a in actions if a.get("priority") == "critical"),
                "high": sum(1 for a in actions if a.get("priority") == "high"),
                "medium": sum(1 for a in actions if a.get("priority") == "medium"),
                "insights_discovered": analysis.get("insights_count", 0),
                "patterns_found": len(analysis.get("patterns", [])),
                "anomalies_detected": len(analysis.get("anomalies", []))
            },
            "observations": observations,
            "analysis": analysis,
            "actions": actions
        }
    
    except Exception as e:
        await ooda.fail_cycle(str(e))
        raise


def create_analysis_structure() -> Dict[str, Any]:
    """Create empty analysis structure for ORIENT phase"""
    return {
        "insights_count": 0,
        "patterns": [],
        "anomalies": [],
        "trends": {}
    }


def add_pattern(analysis: Dict[str, Any], pattern_type: str, data: Dict[str, Any]):
    """Add a pattern to analysis"""
    analysis["patterns"].append({
        "type": pattern_type,
        **data
    })
    analysis["insights_count"] += 1


def add_anomaly(analysis: Dict[str, Any], anomaly_type: str, severity: str, data: Dict[str, Any]):
    """Add an anomaly to analysis"""
    analysis["anomalies"].append({
        "type": anomaly_type,
        "severity": severity,
        **data
    })
    analysis["insights_count"] += 1


def create_action(
    action_id: str,
    action_type: str,
    priority: str,
    title: str,
    description: str,
    action: str,
    expected_outcome: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """Create a standardized action for DECIDE phase"""
    return {
        "id": action_id,
        "type": action_type,
        "priority": priority,
        "title": title,
        "description": description,
        "action": action,
        "expected_outcome": expected_outcome,
        "status": "pending",
        **kwargs
    }
