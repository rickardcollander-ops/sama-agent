"""
Tech (web-dev) agent.

Suggests technical improvements for the customer's website (SEO meta tags,
structured data, performance fixes, accessibility, copy tweaks) and turns
each suggestion into a Pull Request against the GitHub repo configured in
Settings. Never pushes directly to the target branch — always opens a PR
the customer reviews.
"""

import json
import logging
import re
import time
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.config import settings
from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


# ── Models ──────────────────────────────────────────────────────────────────

class SuggestRequest(BaseModel):
    brand_name: str = ""
    domain: str = ""
    brand_description: str = ""
    target_audience: str = ""
    focus: Optional[str] = None  # "seo" | "performance" | "accessibility" | "copy"


class FileChange(BaseModel):
    path: str
    content: str  # full file contents after the change


class ExecuteRequest(BaseModel):
    title: str
    description: str
    file_hint: Optional[str] = None
    change_type: Optional[str] = None  # "edit" | "create" | "delete"
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


# Files we are willing to read/write. Anything matching this allowlist is
# treated as safe. Source files outside this list are rejected to keep the
# agent narrowly scoped to website-content changes.
_ALLOWED_PATTERNS = [
    r"\.(html?|md|mdx|tsx?|jsx?|css|scss|json|ya?ml|txt|svg)$",
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


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/suggest")
async def suggest_tech_changes(payload: SuggestRequest, request: Request):
    """Return 4-6 technical improvement ideas for the site."""
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
            repo_hint = f"GitHub repo: {owner}/{repo} (branch: {gh_config.get('branch', 'main')})\n"

    focus_hint = f"Focus area: {payload.focus}\n" if payload.focus else ""

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = f"""You are a senior web engineer reviewing a marketing website.
Suggest 5 concrete technical improvements that could be implemented as small
self-contained Pull Requests. Cover SEO, structured data, performance,
accessibility, and on-page copy — pick whichever apply.

Brand: {payload.brand_name}
Website: {payload.domain}
Description: {payload.brand_description}
Target audience: {payload.target_audience}
{repo_hint}{focus_hint}
For each suggestion, give:
- title: short, imperative ("Add OpenGraph meta tags to homepage")
- description: 2-3 sentences explaining what to change and why
- file_hint: best guess of the file path likely to need editing
- change_type: "edit" or "create"

Return ONLY a JSON array (no markdown, no code fences):
[
  {{"title": "...", "description": "...", "file_hint": "...", "change_type": "edit|create"}}
]
"""
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        try:
            suggestions = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                suggestions = json.loads(text.strip())
            else:
                suggestions = []

        return {
            "suggestions": suggestions if isinstance(suggestions, list) else [],
            "github_connected": bool(gh_config and gh_config.get("github_token")),
        }
    except Exception as e:
        logger.error(f"suggest_tech_changes error: {e}")
        return {"suggestions": [], "github_connected": False, "error": str(e)}


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


@router.post("/execute")
async def execute_tech_change(payload: ExecuteRequest, request: Request):
    """
    Turn a suggestion into a Pull Request.

    If `files` is provided we use those verbatim. Otherwise we ask Claude to
    propose the file change(s), validate the path allowlist, then create a
    branch + commit + PR via the user's stored GitHub token.
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
            # Ask Claude to produce the file change. Read the current file
            # first if there's a hint so the model has real context.
            current = ""
            target_path = (payload.file_hint or "").lstrip("/")
            if target_path and _is_path_allowed(target_path):
                try:
                    current = await _read_file(http, owner, repo, target_path, base_branch, token) or ""
                except HTTPException:
                    current = ""

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
                files = [FileChange(path=f["path"], content=f["content"]) for f in raw_files if f.get("path")]
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

        # Open the PR
        pr_resp = await http.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=_gh_headers(token),
            json={
                "title": f"[SAMA] {payload.title}",
                "body": (
                    f"{payload.description}\n\n"
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
    Used for manual mode (no GitHub) and PDF export.
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
            summary = result.get("summary", payload.title)
        except Exception as e:
            logger.error(f"preview_tech_change error: {e}")
            raise HTTPException(status_code=500, detail=f"Could not generate preview: {e}")

    return {
        "title": payload.title,
        "description": payload.description,
        "files": [{"path": f.path, "content": f.content} for f in files],
        "summary": payload.title,
    }
