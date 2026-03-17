"""
Proactive Agent Monitor Loop
Runs lightweight "watcher" checks at regular intervals.
When a trigger fires, it kicks off a full OODA cycle or sends an alert.
"""

import asyncio
import logging
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from shared.database import get_supabase

logger = logging.getLogger(__name__)


@dataclass
class TriggerEvent:
    """Describes a detected change that warrants agent action."""
    agent_name: str
    trigger_type: str          # e.g. "keyword_drop", "negative_review", "cpa_spike"
    severity: str              # "low", "medium", "high", "critical"
    description: str
    data: Dict[str, Any] = field(default_factory=dict)
    should_run_ooda: bool = False


class AgentMonitorLoop:
    """
    Registers lightweight watcher functions per agent and runs them
    on a configurable interval.  Watchers return None (nothing interesting)
    or a TriggerEvent when something needs attention.
    """

    def __init__(self):
        self._watchers: List[Dict[str, Any]] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def register_watcher(
        self,
        agent_name: str,
        name: str,
        check_fn: Callable[[], Any],   # async () -> Optional[TriggerEvent]
        interval_minutes: int = 30,
    ):
        self._watchers.append({
            "agent_name": agent_name,
            "name": name,
            "check_fn": check_fn,
            "interval_minutes": interval_minutes,
            "last_run": None,
        })
        logger.info(f"[monitor] Registered watcher: {agent_name}/{name} (every {interval_minutes}m)")

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[monitor] Started with {len(self._watchers)} watchers")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[monitor] Stopped")

    async def _loop(self):
        while self._running:
            for watcher in self._watchers:
                now = datetime.utcnow()
                last = watcher["last_run"]
                interval = timedelta(minutes=watcher["interval_minutes"])

                if last and (now - last) < interval:
                    continue

                try:
                    trigger = await watcher["check_fn"]()
                    watcher["last_run"] = now

                    if trigger and isinstance(trigger, TriggerEvent):
                        await self._handle_trigger(trigger)
                except Exception as e:
                    logger.error(
                        f"[monitor] Watcher {watcher['agent_name']}/{watcher['name']} "
                        f"failed: {e}"
                    )

            # Sleep 60s between scan rounds
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    async def _handle_trigger(self, trigger: TriggerEvent):
        """Log trigger to DB and optionally kick off an OODA cycle."""
        logger.info(
            f"[monitor] TRIGGER {trigger.severity.upper()}: "
            f"{trigger.agent_name}/{trigger.trigger_type} — {trigger.description}"
        )

        # Persist to Supabase
        try:
            sb = get_supabase()
            sb.table("agent_triggers").insert({
                "agent_name": trigger.agent_name,
                "trigger_type": trigger.trigger_type,
                "severity": trigger.severity,
                "description": trigger.description,
                "data": trigger.data,
                "should_run_ooda": trigger.should_run_ooda,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[monitor] Failed to persist trigger (table may not exist): {e}")

        # Publish to event bus if available
        try:
            from shared.event_bus_registry import get_event_bus
            bus = get_event_bus()
            if bus:
                await bus.publish(
                    event_type=f"trigger_{trigger.trigger_type}",
                    target_agent=f"sama_{trigger.agent_name}",
                    data={
                        "trigger_type": trigger.trigger_type,
                        "severity": trigger.severity,
                        "description": trigger.description,
                        **trigger.data,
                    },
                )
        except Exception as e:
            logger.debug(f"[monitor] Could not publish trigger event: {e}")


# ── Built-in watcher functions ───────────────────────────────────────────────

async def check_keyword_drops() -> Optional[TriggerEvent]:
    """SEO watcher: detect keywords that dropped > 5 positions."""
    try:
        sb = get_supabase()
        result = sb.table("seo_keywords") \
            .select("keyword, current_position, previous_position") \
            .not_.is_("previous_position", "null") \
            .not_.is_("current_position", "null") \
            .execute()

        for kw in (result.data or []):
            cur = kw.get("current_position", 0) or 0
            prev = kw.get("previous_position", 0) or 0
            if cur > 0 and prev > 0 and (cur - prev) >= 5:
                return TriggerEvent(
                    agent_name="seo",
                    trigger_type="keyword_drop",
                    severity="high",
                    description=f'"{kw["keyword"]}" dropped from {prev} to {cur}',
                    data={"keyword": kw["keyword"], "old": prev, "new": cur},
                    should_run_ooda=True,
                )
    except Exception as e:
        logger.debug(f"[watcher] keyword_drops check failed: {e}")
    return None


async def check_negative_reviews() -> Optional[TriggerEvent]:
    """Reviews watcher: detect new reviews with rating <= 2."""
    try:
        sb = get_supabase()
        cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        result = sb.table("reviews") \
            .select("platform, rating, title, created_at") \
            .lte("rating", 2) \
            .gte("created_at", cutoff) \
            .execute()

        if result.data and len(result.data) > 0:
            r = result.data[0]
            return TriggerEvent(
                agent_name="reviews",
                trigger_type="negative_review",
                severity="critical",
                description=f'{r.get("rating")}-star review on {r.get("platform", "unknown")}',
                data=r,
                should_run_ooda=False,
            )
    except Exception as e:
        logger.debug(f"[watcher] negative_reviews check failed: {e}")
    return None


async def check_traffic_anomaly() -> Optional[TriggerEvent]:
    """Analytics watcher: today's traffic vs 7-day average."""
    try:
        sb = get_supabase()
        cutoff = (datetime.utcnow() - timedelta(days=8)).isoformat()
        result = sb.table("daily_metrics") \
            .select("date, total_sessions") \
            .gte("date", cutoff) \
            .order("date", desc=True) \
            .limit(8) \
            .execute()

        data = result.data or []
        if len(data) < 3:
            return None

        today = data[0].get("total_sessions", 0) or 0
        past = [d.get("total_sessions", 0) or 0 for d in data[1:]]
        avg = sum(past) / len(past) if past else 0

        if avg > 0 and today < avg * 0.7:
            drop_pct = round((1 - today / avg) * 100, 1)
            return TriggerEvent(
                agent_name="analytics",
                trigger_type="traffic_drop",
                severity="high",
                description=f"Traffic {drop_pct}% below 7-day average ({today} vs {avg:.0f})",
                data={"today": today, "average": avg, "drop_pct": drop_pct},
                should_run_ooda=True,
            )
    except Exception as e:
        logger.debug(f"[watcher] traffic_anomaly check failed: {e}")
    return None


async def check_ads_cpa_spike() -> Optional[TriggerEvent]:
    """Ads watcher: flag campaigns with CPA > 2x target."""
    try:
        sb = get_supabase()
        result = sb.table("ad_campaigns") \
            .select("name, cpa, target_cpa, daily_spend, daily_budget") \
            .eq("status", "active") \
            .execute()

        for c in (result.data or []):
            cpa = c.get("cpa", 0) or 0
            target = c.get("target_cpa", 0) or 0
            if target > 0 and cpa > target * 2:
                return TriggerEvent(
                    agent_name="ads",
                    trigger_type="cpa_spike",
                    severity="high",
                    description=f'Campaign "{c["name"]}" CPA ${cpa:.2f} (target ${target:.2f})',
                    data=c,
                    should_run_ooda=True,
                )
    except Exception as e:
        logger.debug(f"[watcher] ads_cpa check failed: {e}")
    return None


# ── Convenience: register all built-in watchers ──────────────────────────────

def register_default_watchers(monitor: AgentMonitorLoop):
    monitor.register_watcher("seo", "keyword_drops", check_keyword_drops, interval_minutes=60)
    monitor.register_watcher("reviews", "negative_reviews", check_negative_reviews, interval_minutes=15)
    monitor.register_watcher("analytics", "traffic_anomaly", check_traffic_anomaly, interval_minutes=30)
    monitor.register_watcher("ads", "cpa_spike", check_ads_cpa_spike, interval_minutes=30)
