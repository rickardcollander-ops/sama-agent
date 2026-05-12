"""
Domain comparison helpers used to guard tenant data against cross-domain leaks.

The SAMA backend persists runs keyed by ``tenant_id``, but production has
historical rows where the tenant_id/domain pairing drifted (legacy
single-tenant rows, view-as bleed, accidental cross-workspace audits). The
dashboard now refuses to surface a run whose ``domain`` does not match the
workspace's configured ``user_sites.settings.domain``; the backend mirrors
that guarantee at the source so a misconfigured caller can't poison the
cache or trip the read filter.

``normalize_host`` is intentionally lenient: it strips scheme, ``www.``,
trailing slash and any path so values stored across the lifetime of the
table all collapse to the same bare host.
"""

from __future__ import annotations

import re

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def normalize_host(value: str | None) -> str:
    """Return the bare lowercase host for ``value`` (``""`` when unparseable).

    Mirrors the dashboard's ``lib/domain.ts`` normaliser so comparisons match
    on both sides: case-insensitive, ``www.`` stripped, scheme stripped,
    path / query / fragment dropped.
    """
    if not value or not isinstance(value, str):
        return ""
    host = value.strip().lower()
    if not host:
        return ""
    host = _SCHEME_RE.sub("", host)
    # Drop user-info (rare on these inputs but cheap to support).
    if "@" in host:
        host = host.split("@", 1)[1]
    # Strip path/query/fragment — anything after the first '/', '?' or '#'.
    for sep in ("/", "?", "#"):
        if sep in host:
            host = host.split(sep, 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def same_domain(a: str | None, b: str | None) -> bool:
    """True when ``a`` and ``b`` resolve to the same bare host.

    Returns ``False`` when either side is empty — callers that want to skip
    the guard for tenants without a configured domain should branch on
    ``normalize_host(expected)`` themselves before invoking this.
    """
    na = normalize_host(a)
    nb = normalize_host(b)
    if not na or not nb:
        return False
    return na == nb
