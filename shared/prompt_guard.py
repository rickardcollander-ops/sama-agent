"""
Prompt-injection guard for user-supplied input that ends up in LLM calls.

Two layers:
  1. ``wrap_user_content`` — wraps the value in an XML tag (``<user_input>``)
     and HTML-escapes ``&``, ``<``, ``>`` so the model can never see a literal
     closing tag inside the data. The system prompt should instruct the model
     to treat the contents of ``<user_input>`` as data, not instructions.
  2. ``scan`` — heuristic regex match against known injection patterns
     ("ignore previous", "system:", impersonation tags, jailbreak phrasings).
     A match doesn't block the call (false positives are common) — it tags
     the run so the orchestrator can require human approval before any
     state-changing action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as _html_escape


@dataclass(frozen=True)
class GuardResult:
    suspicious: bool
    reasons: tuple[str, ...]


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous", re.compile(r"\bignore\s+(all\s+)?previous\b", re.I)),
    ("disregard", re.compile(r"\bdisregard\s+(all\s+)?(prior|previous|earlier|above)\b", re.I)),
    ("forget", re.compile(r"\bforget\s+(everything|all|previous|earlier)\b", re.I)),
    ("override_system", re.compile(r"\b(override|bypass|exfiltrate)\s+(system|safety|guardrails?)\b", re.I)),
    ("role_takeover", re.compile(r"</?(system|assistant|user)>", re.I)),
    ("dump_prompt", re.compile(r"\b(dump|reveal|print|show)\s+(your|the)\s+(system\s+)?(prompt|instructions)\b", re.I)),
    ("jailbreak", re.compile(r"\b(DAN\s+mode|developer\s+mode|do\s+anything\s+now)\b", re.I)),
    ("act_as", re.compile(r"\bact\s+as\s+(if|though)\s+you\s+(are|were)\s+(no\s+longer|not)\b", re.I)),
)


def scan(text: str) -> GuardResult:
    """Return a ``GuardResult`` describing how suspicious ``text`` looks.

    The function is best-effort and never raises — callers can assume it's
    safe to invoke with arbitrary user input.
    """
    if not text:
        return GuardResult(False, ())
    reasons: list[str] = []
    for label, pat in _PATTERNS:
        if pat.search(text):
            reasons.append(label)
    return GuardResult(bool(reasons), tuple(reasons))


def wrap_user_content(label: str, value: str) -> str:
    """Wrap user input in an XML-like tag with HTML-escaping.

    The tag name must be alphanumeric+underscore; values are escaped so the
    model never sees a literal ``</label>`` token from the user side.
    """
    safe_label = re.sub(r"[^A-Za-z0-9_]", "_", label) or "user_input"
    safe_value = _html_escape(value or "", quote=False)
    return f"<{safe_label}>\n{safe_value}\n</{safe_label}>"
