"""
Tech (web-dev) agent.

Suggests technical improvements for the customer's website (SEO meta tags,
structured data, performance fixes, accessibility, copy tweaks) and turns
each suggestion into a Pull Request against the GitHub repo configured in
Settings. Never pushes directly to the target branch — always opens a PR
the customer reviews.

Suggestions are anchored to a real site_audit run so Claude can quote the
customer's actual current code (head HTML, og:title, JSON-LD blocks,
robots.txt content) instead of guessing from brand metadata. Each
suggestion carries a target_url, a current_snippet copied from the audit,
and a paste-ready suggested_snippet — when the user clicks "Fix with PR"
we forward those fields to /execute so we never re-prompt the model.
"""

import json
import logging
import re
import time
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.audit_brief import build_audit_brief
from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Snippet caps. Longer than this and we drop to a placeholder so the row
# stays small and the PR diff stays readable.
MAX_SNIPPET_CHARS = 1500


# ── Models ──────────────────────────────────────────────────────────────────

class SuggestRequest(BaseModel):
    # New, preferred path: load the audit run server-side.
    audit_id: Optional[str] = None
    focus: Optional[str] = None  # "seo" | "performance" | "accessibility" | "copy"
    max_suggestions: int = 6
    # Legacy brand metadata kept so any older caller still works. Used only
    # when no audit is available — onboarding always seeds one now, so this
    # path is rare in practice.
    brand_name: str = ""
    domain: str = ""
    brand_description: str = ""
    target_audience: str = ""


class FileChange(BaseModel):
    path: str
    content: str  # full file contents after the change


class TechSuggestion(BaseModel):
    """Returned by /suggest and accepted (optionally) by /execute and /preview.

    Older callers that only filled title/description/file_hint/change_type
    keep working — every new field is optional and the agent treats absent
    snippets as "ask Claude to draft the file" same as before.
    """
    title: str
    description: str
    rationale: Optional[str] = None
    target_url: Optional[str] = None
    file_hint: Optional[str] = None
    change_type: Optional[str] = None  # "edit" | "create" | "delete"
    language: Optional[str] = None  # "html" | "jsx" | "tsx" | "json" | "yaml" | "text" | "md" | "other"
    current_snippet: Optional[str] = None
    suggested_snippet: Optional[str] = None
    finding_ref: Optional[str] = None
    impact: Optional[str] = None  # "low" | "medium" | "high"
    effort: Optional[str] = None  # "low" | "medium" | "high"


class ExecuteRequest(BaseModel):
    title: str
    description: str
    file_hint: Optional[str] = None
    change_type: Optional[str] = None  # "edit" | "create" | "delete"
    # New: when the user already saw a paste-ready snippet on the dashboard,
    # we use it verbatim instead of re-prompting Claude. Halves PR cost and
    # guarantees what they saw is what gets PR-ed.
    target_url: Optional[str] = None
    language: Optional[str] = None
    current_snippet: Optional[str] = None
    suggested_snippet: Optional[str] = None
    # Optional pre-computed file changes; if absent the agent generates them
    files: Optional[List[FileChange]] = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:60] if s else "tech-change"


def _load_settings(tenant_id: str) -> dict:
    try:
        sb = get_supabase()
        data = sb.table("user_settings").select("settings").eq("user_id", tenant_id).single().execute()
        return data.data.get("settings", {}) if data.data else {}
    except Exception:
        return {}


def _get_github_config(tenant_id: str) -> Optional[dict]:
    s = _load_settings(tenant_id)
    return s.get("github_integration")


def _load_audit_run(tenant_id: str, audit_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch a stored site_audit payload for this tenant.

    If ``audit_id`` is given we load that one; otherwise we grab the most
    recent completed run (falls through to None when none exist). We always
    verify ``tenant_id`` matches so a leaked id from another tenant can't be
    used to read someone else's audit.
    """
    try:
        sb = get_supabase()
        q = (
            sb.table("site_audits")
            .select("id,payload,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "completed")
        )
        if audit_id:
            q = q.eq("id", audit_id)
        else:
            q = q.order("started_at", desc=True)
        result = q.limit(1).execute()
        rows = result.data or []
        if not rows:
            return None
        payload = rows[0].get("payload") or {}
        if not isinstance(payload, dict) or not payload:
            return None
        # Stamp the id back on so the caller can attribute the response.
        payload.setdefault("id", rows[0].get("id"))
        return payload
    except Exception as e:
        logger.warning(f"_load_audit_run failed for tenant={tenant_id}: {e}")
        return None


# Files we are willing to read/write. Anything matching this allowlist is
# treated as safe. Source files outside this list are rejected to keep the
# agent narrowly scoped to website-content changes.
_ALLOWED_PATTERNS = [
    r"\.(html?|md|mdx|tsx?|jsx?|css|scss|json|ya?ml|toml|txt|svg)$",
    r"(^|/)_headers$",
    r"(^|/)_redirects$",
    r"^public/.*",
    r"^src/.*",
    r"^app/.*",
    r"^content/.*",
    r"^pages/.*",
    r"^components/.*",
    r"^assets/.*",
]


def _is_path_allowed(path: str) -> bool:
    p = path.lstrip("/")
    if ".." in p or p.startswith("."):
        return False
    return any(re.search(pat, p) for pat in _ALLOWED_PATTERNS)


def _clamp_snippet(s: Optional[str]) -> Optional[str]:
    """Cap snippets so a hallucinating model can't bloat a single suggestion
    into the entire response. Returns None for empty/whitespace input."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    if len(s) > MAX_SNIPPET_CHARS:
        return s[:MAX_SNIPPET_CHARS] + "\n…(truncated)"
    return s


def _validate_suggestion(raw: Dict[str, Any], allowed_urls: set[str]) -> Optional[Dict[str, Any]]:
    """Coerce + sanity-check a raw suggestion dict from Claude.

    Drops suggestions without a title or with a target_url Claude invented
    (we only allow URLs Claude saw in the brief — otherwise it's free to
    hallucinate which page the fix applies to). Truncates snippets.
    """
    if not isinstance(raw, dict):
        return None
    title = (raw.get("title") or "").strip()
    description = (raw.get("description") or "").strip()
    if not title or not description:
        return None
    target_url = (raw.get("target_url") or "").strip() or None
    # If Claude picked a URL we never showed it, drop the field rather than
    # mislead the user — the rest of the suggestion may still be useful.
    if target_url and allowed_urls and target_url not in allowed_urls:
        target_url = None
    change_type = (raw.get("change_type") or "edit").strip().lower()
    if change_type not in ("edit", "create", "delete"):
        change_type = "edit"
    language = (raw.get("language") or "").strip().lower() or None
    if language and language not in (
        "html", "jsx", "tsx", "json", "yaml", "text", "md", "other"
    ):
        language = "other"
    impact = (raw.get("impact") or "").strip().lower() or None
    effort = (raw.get("effort") or "").strip().lower() or None
    if impact not in (None, "low", "medium", "high"):
        impact = None
    if effort not in (None, "low", "medium", "high"):
        effort = None
    return {
        "title": title[:200],
        "description": description[:600],
        "rationale": (raw.get("rationale") or "").strip()[:400] or None,
        "target_url": target_url,
        "file_hint": (raw.get("file_hint") or "").strip() or None,
        "change_type": change_type,
        "language": language,
        "current_snippet": _clamp_snippet(raw.get("current_snippet")),
        "suggested_snippet": _clamp_snippet(raw.get("suggested_snippet")),
        "finding_ref": (raw.get("finding_ref") or "").strip()[:120] or None,
        "impact": impact,
        "effort": effort,
    }


def _parse_json_array(text: str) -> List[Any]:
    """Best-effort extraction of a JSON array from a Claude response.

    Handles bare arrays, ```json fenced blocks, and stray prose around the
    array (we just look for the first [ … ] span).
    """
    text = (text or "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("suggestions"), list):
            return parsed["suggestions"]
    except json.JSONDecodeError:
        pass
    # Strip code fences
    if "```" in text:
        for chunk in text.split("```"):
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("[") or chunk.startswith("{"):
                try:
                    parsed = json.loads(chunk)
                    if isinstance(parsed, list):
                        return parsed
                    if isinstance(parsed, dict) and isinstance(parsed.get("suggestions"), list):
                        return parsed["suggestions"]
                except json.JSONDecodeError:
                    continue
    # Last-ditch: span between first [ and last ]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


# ── Prompts ─────────────────────────────────────────────────────────────────

_SUGGEST_SYSTEM = (
    "You are a senior web engineer producing per-URL fix recommendations for "
    "a real customer website. Every suggestion you return MUST quote the "
    "customer's actual current state (taken verbatim from the audit you are "
    "given) and provide ready-to-paste code that can be dropped straight into "
    "their <head>, JSON-LD block, robots.txt, or component file. Never use "
    "placeholders like 'TODO' or 'YOUR_BRAND_HERE'. If you don't have enough "
    "evidence to write a concrete fix for a particular topic, skip it instead "
    "of writing a generic suggestion."
)


def _build_suggest_user_prompt(
    brief: Dict[str, Any],
    focus: Optional[str],
    max_suggestions: int,
    repo_hint: str,
) -> str:
    """The user-message template fed to Claude.

    The brief is JSON-serialised so the model can index into it deterministically;
    we list the URL allowlist explicitly so it doesn't invent target_url values.
    """
    allowed_urls = [p["url"] for p in brief.get("pages", []) if p.get("url")]
    focus_line = f"Focus area: {focus}\n" if focus else ""
    return f"""You are reviewing the audit below and proposing up to {max_suggestions} concrete technical improvements.

{repo_hint}{focus_line}AUDIT BRIEF (JSON):
{json.dumps(brief, ensure_ascii=False)[:12000]}

ALLOWED target_url VALUES (use exactly one of these, or leave null):
{json.dumps(allowed_urls)}

For each suggestion, return an object with these fields:
- title: short imperative ("Add og:image to homepage")
- description: 2-3 sentences explaining what to change and why it matters for THIS page or site
- rationale: one sentence quoting the measurement that triggered the suggestion (e.g. "title is 12 chars, target 30-65")
- target_url: one of the allowed URLs above, or null for site-wide fixes (robots.txt, security headers)
- file_hint: best-guess source file path (e.g. "app/layout.tsx", "public/robots.txt", "components/Hero.tsx")
- change_type: "edit" | "create" | "delete"
- language: "html" | "jsx" | "tsx" | "json" | "yaml" | "text" | "md" | "other"
- current_snippet: the customer's actual current code, copied VERBATIM from the brief (head_html_excerpt, jsonld_first, og_tags, robots_txt_content, etc.). Null when there is nothing to replace (e.g. add a brand-new tag).
- suggested_snippet: the paste-ready replacement. Drop-in HTML / JSON-LD / meta tag / robots.txt block. No placeholders.
- finding_ref: title of the matching top_findings entry, when applicable
- impact: "low" | "medium" | "high"
- effort: "low" | "medium" | "high"

EXAMPLES of the level of specificity expected:

1) Title issue. The brief shows pages[2].title = "Hem" (3 chars).
   {{"title": "Lengthen the homepage title from 'Hem' to a descriptive value",
     "rationale": "Current <title>Hem</title> is 3 chars; target 30-65 to maximise SERP CTR.",
     "target_url": "https://example.com/",
     "current_snippet": "<title>Hem</title>",
     "suggested_snippet": "<title>Brandname — kort sammanfattning av vad ni gör</title>",
     ...}}

2) Missing og:image. Brief shows og_tags lacks "og:image" but has og:title and og:description.
   {{"title": "Add og:image to homepage so social shares render with a preview",
     "current_snippet": "<meta property=\\"og:title\\" content=\\"…\\">\\n<meta property=\\"og:description\\" content=\\"…\\">",
     "suggested_snippet": "<meta property=\\"og:title\\" content=\\"…\\">\\n<meta property=\\"og:description\\" content=\\"…\\">\\n<meta property=\\"og:image\\" content=\\"https://example.com/og-cover.png\\">\\n<meta property=\\"og:image:width\\" content=\\"1200\\">\\n<meta property=\\"og:image:height\\" content=\\"630\\">",
     ...}}

3) Broken JSON-LD. Brief shows jsonld_first with malformed @type.
   Quote the raw block in current_snippet and return the corrected JSON in suggested_snippet.

Return ONLY a JSON array (no markdown, no code fences, no prose):
[ {{ ... }}, {{ ... }} ]
"""


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/suggest")
async def suggest_tech_changes(payload: SuggestRequest, request: Request):
    """Return up to ``max_suggestions`` technical improvements anchored to a
    real audit run. Falls back to the legacy brand-only prompt only when no
    completed audit exists for this tenant.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")

    if not payload.brand_name and tenant_id != "default":
        s = _load_settings(tenant_id)
        payload.brand_name = payload.brand_name or s.get("brand_name", "")
        payload.domain = payload.domain or s.get("domain", "")
        payload.brand_description = payload.brand_description or s.get("brand_description", "")
        payload.target_audience = payload.target_audience or s.get("target_audience", "")

    gh_config = _get_github_config(tenant_id)
    repo_hint = ""
    if gh_config:
        owner = gh_config.get("repo_owner", "")
        repo = gh_config.get("repo_name", "")
        if owner and repo:
            repo_hint = (
                f"GitHub repo: {owner}/{repo} (branch: {gh_config.get('branch', 'main')})\n"
            )

    run = _load_audit_run(tenant_id, payload.audit_id)
    degraded = run is None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        if not degraded:
            brief = build_audit_brief(run, focus=payload.focus, max_pages=8)
            allowed_urls: set[str] = {
                p["url"] for p in brief.get("pages", []) if p.get("url")
            }
            user_prompt = _build_suggest_user_prompt(
                brief=brief,
                focus=payload.focus,
                max_suggestions=max(1, min(payload.max_suggestions, 10)),
                repo_hint=repo_hint,
            )
            message = client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=4096,
                system=_SUGGEST_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )
        else:
            # Degraded path: brand metadata only. Same JSON shape so the
            # dashboard renders identically (just without snippets).
            focus_hint = f"Focus area: {payload.focus}\n" if payload.focus else ""
            legacy = f"""You are a senior web engineer reviewing a marketing website.
Suggest 5 concrete technical improvements that could be implemented as small
self-contained Pull Requests. Cover SEO, structured data, performance,
accessibility, and on-page copy — pick whichever apply.

Brand: {payload.brand_name}
Website: {payload.domain}
Description: {payload.brand_description}
Target audience: {payload.target_audience}
{repo_hint}{focus_hint}
For each suggestion, return an object with these fields:
- title, description, file_hint, change_type ("edit"|"create")
- impact ("low"|"medium"|"high"), effort ("low"|"medium"|"high")

Return ONLY a JSON array (no markdown):
[ {{ ... }} ]
"""
            message = client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": legacy}],
            )
            allowed_urls = set()

        text = message.content[0].text if message.content else ""
        raw_suggestions = _parse_json_array(text)
        suggestions: List[Dict[str, Any]] = []
        for raw in raw_suggestions:
            cleaned = _validate_suggestion(raw, allowed_urls)
            if cleaned:
                suggestions.append(cleaned)

        return {
            "suggestions": suggestions,
            "github_connected": bool(gh_config and gh_config.get("github_token")),
            # Tells the dashboard to surface a "run an audit for sharper
            # suggestions" banner when we couldn't ground the prompt.
            "degraded": degraded,
            "audit_id": (run or {}).get("id") if run else None,
        }
    except Exception as e:
        logger.error(f"suggest_tech_changes error: {e}")
        return {
            "suggestions": [],
            "github_connected": False,
            "degraded": True,
            "error": str(e),
        }


async def _read_file(client: httpx.AsyncClient, owner: str, repo: str, path: str, branch: str, token: str) -> Optional[str]:
    """Read the current contents of a file from the repo (None if missing)."""
    resp = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        headers=_gh_headers(token),
        params={"ref": branch},
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Could not read {path}: {resp.text[:200]}")
    data = resp.json()
    if isinstance(data, list):
        # path is a directory
        return None
    encoded = data.get("content", "")
    return b64decode(encoded).decode("utf-8", errors="replace")


def _apply_snippet_to_file(
    current: str,
    current_snippet: Optional[str],
    suggested_snippet: Optional[str],
) -> Optional[str]:
    """Apply a snippet edit deterministically when possible.

    Returns the full new file contents when:
      - current_snippet is present and appears verbatim in `current`
        → replace it with suggested_snippet
      - current is empty AND current_snippet is empty → use suggested_snippet
        as the entire new file (covers create-from-scratch cases)
    Returns None when we can't apply deterministically; the caller falls
    back to prompting Claude to draft the file.
    """
    if not suggested_snippet:
        return None
    if not current and not current_snippet:
        return suggested_snippet
    if current_snippet and current_snippet in current:
        return current.replace(current_snippet, suggested_snippet, 1)
    return None


@router.post("/execute")
async def execute_tech_change(payload: ExecuteRequest, request: Request):
    """
    Turn a suggestion into a Pull Request.

    Three input modes, in order of preference:
      1. ``files`` provided → use verbatim, no LLM call.
      2. ``suggested_snippet`` provided + we can splice it into the existing
         file → no LLM call (cheapest, most predictable).
      3. Otherwise, ask Claude to draft the file change with full context.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    gh_config = _get_github_config(tenant_id)
    if not gh_config or not gh_config.get("github_token"):
        raise HTTPException(status_code=400, detail="GitHub not connected. Connect in Settings first.")

    token = gh_config["github_token"]
    owner = gh_config.get("repo_owner") or ""
    repo = gh_config.get("repo_name") or ""
    base_branch = gh_config.get("branch", "main")

    if not owner or not repo:
        raise HTTPException(status_code=400, detail="No repository configured in Settings.")

    files: List[FileChange] = payload.files or []

    async with httpx.AsyncClient(timeout=60.0) as http:
        if not files:
            # Read the hinted file first whether or not we'll re-prompt — the
            # snippet path needs it for splicing, the LLM path needs it for
            # context.
            current = ""
            target_path = (payload.file_hint or "").lstrip("/")
            if target_path and _is_path_allowed(target_path):
                try:
                    current = await _read_file(http, owner, repo, target_path, base_branch, token) or ""
                except HTTPException:
                    current = ""

            spliced = _apply_snippet_to_file(
                current=current,
                current_snippet=payload.current_snippet,
                suggested_snippet=payload.suggested_snippet,
            )
            if spliced is not None and target_path:
                # Path 2 — paste-ready snippet from the dashboard, no LLM.
                files = [FileChange(path=target_path, content=spliced)]
            else:
                # Path 3 — fall back to Claude drafting the change.
                snippet_hint = ""
                if payload.suggested_snippet:
                    snippet_hint = (
                        "\nSuggested replacement snippet (paste this verbatim where it fits):\n"
                        "---\n"
                        f"{payload.suggested_snippet}\n"
                        "---\n"
                    )
                target_url_hint = f"\nThe suggestion targets the page: {payload.target_url}\n" if payload.target_url else ""
                try:
                    import anthropic

                    ai = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
                    prompt = f"""You are a senior web engineer. Implement the following change in a GitHub repo.

Repo: {owner}/{repo}
Target branch: {base_branch}
Suggestion title: {payload.title}
Description: {payload.description}
File hint: {payload.file_hint or 'none'}
Change type: {payload.change_type or 'edit'}
{target_url_hint}{snippet_hint}
Current contents of the hinted file (may be empty if the file does not exist yet):
---
{current[:8000]}
---

Return ONLY a JSON object (no markdown, no code fences) of the form:
{{
  "files": [
    {{"path": "relative/path/from/repo/root.ext", "content": "FULL FILE CONTENTS AFTER THE CHANGE"}}
  ],
  "summary": "one short sentence describing the change"
}}

Rules:
- Do not include explanatory text outside the JSON.
- Always return the FULL file content, not a diff.
- Prefer a single file. Two files only if strictly necessary.
- Keep changes small and reviewable.
"""
                    message = ai.messages.create(
                        model=settings.CLAUDE_MODEL,
                        max_tokens=4096,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = message.content[0].text.strip()
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip().rstrip("`").strip()
                    result = json.loads(text)
                    raw_files = result.get("files", [])
                    files = [
                        FileChange(path=f["path"], content=f["content"])
                        for f in raw_files if f.get("path")
                    ]
                except Exception as e:
                    logger.error(f"execute_tech_change generation error: {e}")
                    raise HTTPException(status_code=500, detail=f"Could not draft file changes: {e}")

        if not files:
            raise HTTPException(status_code=400, detail="No file changes were produced.")

        # Validate paths
        for f in files:
            if not _is_path_allowed(f.path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Refusing to write to {f.path}: outside allowed website paths.",
                )

        # Create a fresh branch off base
        timestamp = int(time.time())
        new_branch = f"sama/tech/{_slugify(payload.title)}-{timestamp}"

        ref_resp = await http.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
            headers=_gh_headers(token),
        )
        if ref_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Could not find base branch '{base_branch}' in {owner}/{repo}",
            )
        base_sha = ref_resp.json()["object"]["sha"]

        create_ref = await http.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
            headers=_gh_headers(token),
            json={"ref": f"refs/heads/{new_branch}", "sha": base_sha},
        )
        if create_ref.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Could not create branch: {create_ref.text[:200]}",
            )

        # Commit each file. Need the existing blob SHA for updates.
        for f in files:
            existing = await http.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/contents/{f.path}",
                headers=_gh_headers(token),
                params={"ref": new_branch},
            )
            sha = existing.json().get("sha") if existing.status_code == 200 else None

            put_body = {
                "message": f"[SAMA tech] {payload.title}",
                "content": b64encode(f.content.encode("utf-8")).decode("ascii"),
                "branch": new_branch,
            }
            if sha:
                put_body["sha"] = sha

            put_resp = await http.put(
                f"{GITHUB_API}/repos/{owner}/{repo}/contents/{f.path}",
                headers=_gh_headers(token),
                json=put_body,
            )
            if put_resp.status_code not in (200, 201):
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not write {f.path}: {put_resp.text[:200]}",
                )

        # Open the PR. Cite the audit URL when available so the reviewer
        # sees which page motivated the change without opening the dashboard.
        body_extras = ""
        if payload.target_url:
            body_extras += f"\n\nApplies to: {payload.target_url}"
        if payload.suggested_snippet and not payload.files:
            body_extras += "\n\nThis PR pastes a SAMA-suggested snippet verbatim — no LLM re-draft."
        pr_resp = await http.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=_gh_headers(token),
            json={
                "title": f"[SAMA] {payload.title}",
                "body": (
                    f"{payload.description}{body_extras}\n\n"
                    f"_Auto-generated by SAMA tech agent. Review the diff before merging._\n\n"
                    f"Files changed:\n" + "\n".join(f"- `{f.path}`" for f in files)
                ),
                "head": new_branch,
                "base": base_branch,
            },
        )
        if pr_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Could not open PR: {pr_resp.text[:200]}",
            )

        pr_data = pr_resp.json()
        return {
            "pr_url": pr_data.get("html_url", ""),
            "branch": new_branch,
            "files_changed": [f.path for f in files],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


@router.post("/preview")
async def preview_tech_change(payload: ExecuteRequest, request: Request):
    """
    Generate file changes for a suggestion and return them without creating a PR.
    Used for manual mode (no GitHub) and PDF export. Same three-mode logic as
    /execute, just without the GitHub side effects.
    """
    tenant_id = getattr(request.state, "tenant_id", "default")
    s = _load_settings(tenant_id)

    gh_config = _get_github_config(tenant_id)
    owner = gh_config.get("repo_owner", "") if gh_config else ""
    repo = gh_config.get("repo_name", "") if gh_config else ""
    base_branch = gh_config.get("branch", "main") if gh_config else "main"
    token = gh_config.get("github_token", "") if gh_config else ""

    domain = s.get("domain", "")
    brand_name = s.get("brand_name", "")

    files: list[FileChange] = payload.files or []

    if not files:
        current = ""
        target_path = (payload.file_hint or "").lstrip("/")

        # Try to read current file from GitHub if connected
        if target_path and _is_path_allowed(target_path) and owner and repo and token:
            try:
                async with httpx.AsyncClient(timeout=30.0) as http:
                    current = await _read_file(http, owner, repo, target_path, base_branch, token) or ""
            except Exception:
                current = ""

        spliced = _apply_snippet_to_file(
            current=current,
            current_snippet=payload.current_snippet,
            suggested_snippet=payload.suggested_snippet,
        )
        if spliced is not None and target_path:
            files = [FileChange(path=target_path, content=spliced)]
        else:
            snippet_hint = ""
            if payload.suggested_snippet:
                snippet_hint = (
                    "\nSuggested replacement snippet (paste this verbatim where it fits):\n"
                    "---\n"
                    f"{payload.suggested_snippet}\n"
                    "---\n"
                )
            target_url_hint = f"\nThe suggestion targets the page: {payload.target_url}\n" if payload.target_url else ""
            try:
                import anthropic

                ai = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
                prompt = f"""You are a senior web engineer. Generate the file changes needed to implement the following improvement.

Website: {domain}
Brand: {brand_name}
Suggestion title: {payload.title}
Description: {payload.description}
File hint: {payload.file_hint or 'none'}
Change type: {payload.change_type or 'edit'}
{target_url_hint}{snippet_hint}
Current contents of the hinted file (may be empty if unknown):
---
{current[:6000]}
---

Return ONLY a JSON object (no markdown, no code fences):
{{
  "files": [
    {{"path": "relative/path/to/file.ext", "content": "FULL FILE CONTENTS AFTER THE CHANGE", "diff_hint": "One sentence describing what changed"}}
  ],
  "summary": "Short summary of the change"
}}

Rules:
- Return full file content, not a diff.
- Prefer a single file. Two files only if strictly necessary.
- Keep changes minimal and reviewable.
- If file hint is unknown, use a sensible generic path.
"""
                message = ai.messages.create(
                    model=settings.CLAUDE_MODEL,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = message.content[0].text.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip().rstrip("`").strip()
                result = json.loads(text)
                raw_files = result.get("files", [])
                files = [FileChange(path=f["path"], content=f["content"]) for f in raw_files if f.get("path")]
            except Exception as e:
                logger.error(f"preview_tech_change error: {e}")
                raise HTTPException(status_code=500, detail=f"Could not generate preview: {e}")

    return {
        "title": payload.title,
        "description": payload.description,
        "files": [{"path": f.path, "content": f.content} for f in files],
        "summary": payload.title,
    }
