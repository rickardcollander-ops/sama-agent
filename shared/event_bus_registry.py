"""
Event Bus Registry — provides a global getter for the active event bus instance.
This avoids circular imports between main.py and other modules.
"""

from typing import Optional, Any

_event_bus: Optional[Any] = None


def set_event_bus(bus):
    global _event_bus
    _event_bus = bus


def get_event_bus():
    return _event_bus
