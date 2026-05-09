"""
Embedding helper.

Wraps the Voyage AI embedding API and exposes a tiny surface used by the
internal-linking optimizer:

    embed_texts(["foo", "bar", ...]) -> List[List[float]]
    cosine_similarity(a, b)          -> float in [-1, 1]

Voyage is the recommended embedding provider for Anthropic stacks. When
``VOYAGE_API_KEY`` is unset every call returns ``None`` and callers must
fall back to keyword matching -- semantic ranking is purely additive.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Sequence

import httpx

from .config import settings

logger = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_MODEL = "voyage-3"
MAX_BATCH = 64
EMBED_TIMEOUT_S = 20.0


def is_configured() -> bool:
    return bool(getattr(settings, "VOYAGE_API_KEY", ""))


async def embed_texts(
    texts: Sequence[str],
    *,
    model: str = DEFAULT_MODEL,
    input_type: str = "document",
) -> Optional[List[List[float]]]:
    """Embed a batch of texts. Returns None when no API key is configured
    or the provider call fails -- callers should treat that as "no semantic
    layer available" and fall back to lexical scoring."""
    if not is_configured():
        return None
    cleaned = [(t or "").strip() for t in texts]
    if not any(cleaned):
        return [[] for _ in cleaned]

    out: List[List[float]] = []
    headers = {
        "Authorization": f"Bearer {settings.VOYAGE_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT_S) as client:
            for i in range(0, len(cleaned), MAX_BATCH):
                batch = cleaned[i : i + MAX_BATCH]
                # Voyage rejects empty strings; substitute a single space so the
                # response slot still aligns with the input index.
                payload_input = [t if t else " " for t in batch]
                resp = await client.post(
                    VOYAGE_URL,
                    headers=headers,
                    json={
                        "input": payload_input,
                        "model": model,
                        "input_type": input_type,
                    },
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                # Voyage returns rows in input order with an "index" field; sort
                # defensively in case a future API version reorders them.
                data.sort(key=lambda row: row.get("index", 0))
                out.extend(row.get("embedding", []) for row in data)
    except Exception as e:
        logger.warning("Voyage embed call failed (%s); falling back to lexical only", e)
        return None
    return out


async def embed_text(text: str, *, input_type: str = "query") -> Optional[List[float]]:
    result = await embed_texts([text], input_type=input_type)
    if not result:
        return None
    vec = result[0]
    return vec or None


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
