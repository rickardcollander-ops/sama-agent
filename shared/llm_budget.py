"""
Per-request token budget + LLM timeout.

Wraps Anthropic ``client.messages.create`` calls so a single tenant can't
burn unlimited credits via a runaway prompt. Three guardrails:

  1. **Pre-call input estimate.** A coarse character→token heuristic (1 token
     ≈ 4 chars) rejects oversized prompts before they reach the network.
     Tunable via ``MAX_INPUT_TOKENS`` (default 8000).
  2. **Hard ``max_tokens`` ceiling.** Default 4096; can be lowered per call
     but never raised above ``MAX_OUTPUT_TOKENS_HARD_CAP``.
  3. **Wall-clock timeout** (``LLM_TIMEOUT_SECONDS``, default 45). Prevents a
     stuck connection from holding a worker indefinitely.

Use via the ``with_budget`` decorator OR call ``check_input_budget`` /
``clamp_max_tokens`` directly when you need finer control.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_INPUT_TOKENS = _int_env("MAX_INPUT_TOKENS", 8000)
MAX_OUTPUT_TOKENS_DEFAULT = _int_env("MAX_OUTPUT_TOKENS_DEFAULT", 4096)
MAX_OUTPUT_TOKENS_HARD_CAP = _int_env("MAX_OUTPUT_TOKENS_HARD_CAP", 8192)
LLM_TIMEOUT_SECONDS = _float_env("LLM_TIMEOUT_SECONDS", 45.0)


class TokenBudgetExceeded(Exception):
    """Raised before the LLM call when input is too large."""


def estimate_tokens(text: str) -> int:
    """Coarse char→token heuristic. Off by ±20% but good enough for a guard."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(str(block.get("text", "")))
    return total


def check_input_budget(
    messages: list[dict[str, Any]],
    system: Any = None,
    limit: int | None = None,
) -> int:
    """Validate input fits the budget. Returns the estimated token count."""
    cap = limit if limit is not None else MAX_INPUT_TOKENS
    est = estimate_messages_tokens(messages)
    if isinstance(system, str):
        est += estimate_tokens(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                est += estimate_tokens(str(block.get("text", "")))
    if est > cap:
        raise TokenBudgetExceeded(
            f"input too large: estimated {est} tokens > limit {cap}"
        )
    return est


def clamp_max_tokens(requested: int | None) -> int:
    if not requested:
        return MAX_OUTPUT_TOKENS_DEFAULT
    return max(1, min(int(requested), MAX_OUTPUT_TOKENS_HARD_CAP))


def with_budget(
    *,
    input_limit: int | None = None,
    timeout: float | None = None,
):
    """Decorator that enforces input/output budgets and a wall-clock timeout
    around an async function that issues a Claude call.

    The decorated function must accept ``messages`` and (optionally)
    ``system`` and ``max_tokens`` kwargs.
    """
    import asyncio

    eff_timeout = timeout if timeout is not None else LLM_TIMEOUT_SECONDS

    def decorator(fn: Callable[..., Awaitable[Any]]):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            messages = kwargs.get("messages") or (args[0] if args else [])
            system = kwargs.get("system")
            check_input_budget(messages or [], system, limit=input_limit)
            kwargs["max_tokens"] = clamp_max_tokens(kwargs.get("max_tokens"))
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=eff_timeout)
            except asyncio.TimeoutError:
                logger.warning("llm_timeout fn=%s timeout=%.1fs", fn.__name__, eff_timeout)
                raise
        return wrapper

    return decorator
