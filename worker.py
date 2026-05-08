"""
Background worker entry-point.

Runs the APScheduler + agent monitor + event bus consumer outside of the web
processes so we can scale ``web`` horizontally without duplicating jobs.

Deployment (Railway): one ``web`` service running ``uvicorn main:app`` and
one ``worker`` service running ``python worker.py``. The worker stays a
single replica (replicas=1) so APScheduler doesn't need a clustered backend.

Env contract:
    RUN_BACKGROUND_JOBS=1     (set automatically by this entry-point)
    WORKER_LOCK_TTL_S=300     advisory-lock TTL when multiple workers race

A best-effort Redis advisory lock (``SET worker:active NX EX``) prevents two
workers from running simultaneously during a rolling deploy. If Redis is not
configured the lock is skipped and we trust the deployment topology.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("worker")


async def _try_acquire_lock() -> tuple[bool, asyncio.Task | None]:
    """Acquire ``worker:active`` lock if Redis is available; refresh it
    periodically while we hold it."""
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        return True, None
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis.asyncio not installed; skipping advisory lock")
        return True, None
    ttl = int(os.getenv("WORKER_LOCK_TTL_S", "300"))
    client = aioredis.from_url(redis_url, decode_responses=True)
    pid = os.getpid()
    got = await client.set("worker:active", str(pid), nx=True, ex=ttl)
    if not got:
        owner = await client.get("worker:active")
        logger.error("another worker holds the lock pid=%s; exiting", owner)
        return False, None

    async def _refresh() -> None:
        while True:
            try:
                await asyncio.sleep(ttl // 3)
                await client.set("worker:active", str(pid), xx=True, ex=ttl)
            except Exception as e:
                logger.warning("lock_refresh_failed err=%s", e)

    return True, asyncio.create_task(_refresh())


async def amain() -> int:
    os.environ["RUN_BACKGROUND_JOBS"] = "1"

    acquired, refresh_task = await _try_acquire_lock()
    if not acquired:
        return 1

    # Reuse the FastAPI lifespan so we get the same event_bus/monitor wiring
    # without copy-pasting initialisation code.
    from main import app

    async with app.router.lifespan_context(app):
        logger.info("worker ready; sleeping until SIGTERM")
        stop = asyncio.Event()

        def _stop(*_: object) -> None:
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:
                # Windows; fall through. Worker isn't deployed there anyway.
                pass

        await stop.wait()

    if refresh_task:
        refresh_task.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
