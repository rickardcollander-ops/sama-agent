"""
Standardized API response helpers.
Use these wrappers to ensure consistent response format across all routes.
"""

from datetime import datetime
from typing import Any, Optional


def success_response(data: Any = None, message: Optional[str] = None) -> dict:
    return {
        "success": True,
        "data": data,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }


def error_response(message: str, code: Optional[str] = None) -> dict:
    return {
        "success": False,
        "error": message,
        "code": code,
        "timestamp": datetime.utcnow().isoformat(),
    }
