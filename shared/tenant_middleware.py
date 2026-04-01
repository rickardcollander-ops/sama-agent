"""
Tenant Middleware for FastAPI
Extracts tenant_id from incoming requests and stores it on request.state.
"""

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Extracts tenant_id from the request and attaches it to ``request.state.tenant_id``.

    Resolution order:
    1. ``X-Tenant-ID`` header
    2. ``tenant_id`` query parameter
    3. Falls back to ``"default"``
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Header
        tenant_id = request.headers.get("X-Tenant-ID")

        # 2. Query param
        if not tenant_id:
            tenant_id = request.query_params.get("tenant_id")

        # 3. Fallback
        if not tenant_id:
            tenant_id = DEFAULT_TENANT_ID

        request.state.tenant_id = tenant_id

        response = await call_next(request)
        return response
