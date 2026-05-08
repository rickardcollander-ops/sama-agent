"""
Global concurrency cap for LLM calls.

Anthropic + every other LLM provider rate-limits per-account, not per-process.
With 6 agents × N tenants we can fan out hundreds of simultaneous calls and
hit 429s en masse, which then become user-visible failures. The semaphore
caps total in-flight calls to ``LLM_CONCURRENCY`` (default 30) so the rest
queue up gracefully.

A tenant-level sub-semaphore ensures one noisy tenant can't starve every
other tenant. ``LLM_PER_TENANT_CONCURRENCY`` (default 6) limits per-tenant
in-flight calls.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


_GLOBAL_LIMIT = _int_env("LLM_CONCURRENCY", 30)
_TENANT_LIMIT = _int_env("LLM_PER_TENANT_CONCURRENCY", 6)

_global_sem: asyncio.Semaphore | None = None
_tenant_sems: Dict[str, asyncio.Semaphore] = {}
_lock: asyncio.Lock | None = None


def _ensure() -> tuple[asyncio.Semaphore, asyncio.Lock]:
    global _global_sem, _lock
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(_GLOBAL_LIMIT)
    if _lock is None:
        _lock = asyncio.Lock()
    return _global_sem, _lock


async def _tenant_semaphore(tenant_id: str) -> asyncio.Semaphore:
    _, lock = _ensure()
    async with lock:
        sem = _tenant_sems.get(tenant_id)
        if sem is None:
            sem = asyncio.Semaphore(_TENANT_LIMIT)
            _tenant_sems[tenant_id] = sem
        return sem


@asynccontextmanager
async def acquire(tenant_id: str = "default") -> AsyncIterator[None]:
    """Acquire one global slot and one tenant-scoped slot."""
    glb, _ = _ensure()
    tenant = await _tenant_semaphore(tenant_id)
    async with tenant:
        async with glb:
            yield
