"""
Single entry point for Anthropic LLM calls.

Bundles four guardrails:
  - **Prompt caching** — system prompt is wrapped in ``cache_control:ephemeral``
    so the same system prompt across N calls is charged once at the cache
    rate, ~70% cost reduction at our usage profile.
  - **Token budget** — input estimate vs ``MAX_INPUT_TOKENS``, ``max_tokens``
    clamped against ``MAX_OUTPUT_TOKENS_HARD_CAP``.
  - **Concurrency cap** — global + per-tenant semaphores in ``llm_pool``.
  - **Wall-clock timeout** — ``LLM_TIMEOUT_SECONDS`` via ``asyncio.wait_for``.

Usage from an agent::

    from shared.llm import call_claude
    response = await call_claude(
        client=self.client,
        model=self.model,
        system="You are ...",
        messages=[{"role": "user", "content": prompt}],
        tenant_id=ctx.tenant_id,
        max_tokens=2048,
    )

The synchronous helper ``call_claude_sync`` does the same minus the
concurrency semaphore — for callers that aren't in async context.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Sequence

from shared.llm_budget import (
    LLM_TIMEOUT_SECONDS,
    TokenBudgetExceeded,
    check_input_budget,
    clamp_max_tokens,
)
from shared.llm_pool import acquire as acquire_llm_slot

logger = logging.getLogger(__name__)


def _cacheable_system(system: str | Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert a system prompt to the structured ``[{type, text, cache_control}]``
    form so Anthropic prompt caching applies."""
    if system is None:
        return []
    if isinstance(system, str):
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    blocks: list[dict[str, Any]] = []
    for b in system:
        if isinstance(b, dict):
            block = dict(b)
            block.setdefault("type", "text")
            block.setdefault("cache_control", {"type": "ephemeral"})
            blocks.append(block)
    return blocks


async def call_claude(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    system: str | Sequence[dict[str, Any]] | None = None,
    max_tokens: int = 2048,
    tenant_id: str = "default",
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Async Claude call with all guardrails applied. Returns the raw
    Anthropic response object."""
    sys_blocks = _cacheable_system(system)
    check_input_budget(messages, system=sys_blocks)
    bounded_max = clamp_max_tokens(max_tokens)

    def _call() -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": bounded_max,
            "messages": messages,
            "timeout": LLM_TIMEOUT_SECONDS,
        }
        if sys_blocks:
            kwargs["system"] = sys_blocks
        if extra_kwargs:
            kwargs.update(extra_kwargs)
        return client.messages.create(**kwargs)

    async with acquire_llm_slot(tenant_id):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_call), timeout=LLM_TIMEOUT_SECONDS + 5
            )
        except asyncio.TimeoutError:
            logger.warning("llm_timeout model=%s tenant=%s", model, tenant_id)
            raise


def call_claude_sync(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    system: str | Sequence[dict[str, Any]] | None = None,
    max_tokens: int = 2048,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Sync variant. Use only from threads/jobs that aren't async."""
    sys_blocks = _cacheable_system(system)
    check_input_budget(messages, system=sys_blocks)
    bounded_max = clamp_max_tokens(max_tokens)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": bounded_max,
        "messages": messages,
        "timeout": LLM_TIMEOUT_SECONDS,
    }
    if sys_blocks:
        kwargs["system"] = sys_blocks
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    return client.messages.create(**kwargs)
