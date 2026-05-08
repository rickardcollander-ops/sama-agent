"""
SSRF-resistant HTTP client.

Wraps ``httpx`` with three guardrails on every outbound request initiated by
agent code (scrapers, OAuth callbacks, third-party API enrichers):

  1. **Scheme allowlist**       — only ``http`` / ``https`` (no ``file://``,
                                  ``gopher://``, internal-only schemes).
  2. **DNS-resolved IP allowlist** — every resolved address must be a global
                                  unicast IP. Loopback (127/8, ::1), link-local
                                  (169.254/16, fe80::/10), private (10/8,
                                  172.16/12, 192.168/16, fc00::/7), and
                                  reserved ranges are blocked. This catches
                                  cloud-metadata SSRF (169.254.169.254) and
                                  internal-service pivots.
  3. **Body + time caps**      — ``DEFAULT_TIMEOUT`` seconds and
                                  ``DEFAULT_MAX_BYTES`` content-length so a
                                  hostile server can't tarpit a worker.

Use ``safe_get(url, ...)`` / ``safe_post(url, ...)`` exactly like
``httpx.get`` / ``httpx.post``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = float(os.getenv("OUTBOUND_HTTP_TIMEOUT_S", "10"))
DEFAULT_MAX_BYTES = int(os.getenv("OUTBOUND_HTTP_MAX_BYTES", str(5 * 1024 * 1024)))

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF allowlist."""


def _is_safe_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return False
    if addr.is_private or addr.is_reserved or addr.is_unspecified:
        return False
    return True


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"DNS lookup failed for {host}: {e}")
    return [info[4][0] for info in infos]


def assert_safe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("missing host")
    # Block direct IP-literal targets in private space too — cuts off
    # http://10.0.0.1/ probes that would otherwise pass DNS.
    try:
        addr = ipaddress.ip_address(host)
        if not _is_safe_ip(str(addr)):
            raise UnsafeURLError(f"target IP {host} is private/loopback/reserved")
    except ValueError:
        pass
    for ip in _resolve(host):
        if not _is_safe_ip(ip):
            raise UnsafeURLError(
                f"host {host} resolves to {ip} which is private/loopback/reserved"
            )


async def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    assert_safe_url(url)
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    max_bytes = kwargs.pop("max_bytes", DEFAULT_MAX_BYTES)

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, http2=False
    ) as client:
        # Disable redirects — every redirect target must be re-validated.
        # We follow up to 5 hops manually so a 301 to 169.254.169.254 is caught.
        next_url = url
        for _ in range(5):
            assert_safe_url(next_url)
            resp = await client.request(method, next_url, **kwargs)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if not loc:
                    return resp
                next_url = httpx.URL(next_url).join(loc).human_repr()
                continue
            # Read body up to cap.
            body = b""
            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                body += chunk
                if len(body) > max_bytes:
                    raise UnsafeURLError(
                        f"response from {next_url} exceeded {max_bytes} bytes"
                    )
            resp._content = body  # type: ignore[attr-defined]  # populate body for caller
            return resp
        raise UnsafeURLError("too many redirects")


async def safe_get(url: str, **kwargs: Any) -> httpx.Response:
    return await _request("GET", url, **kwargs)


async def safe_post(url: str, **kwargs: Any) -> httpx.Response:
    return await _request("POST", url, **kwargs)
