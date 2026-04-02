"""
GitHub Integration API Routes
Connect customer GitHub repos and publish content as Pull Requests.
"""

import logging
import re
import time
from base64 import b64encode
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


# ── Request / Response models ───────────────────────────────────────────────

class ConnectRequest(BaseModel):
    github_token: str
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    blog_path: str = "content/blog"
    branch: str = "main"


class PublishRequest(BaseModel):
    content_id: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    slug: Optional[str] = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _mask_token(token: str) -> str:
    if not token or len(token) < 8:
        return "***"
    return token[:4] + "*" * (len(token) - 8) + token[-4:]


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[åä]", "a", slug)
    slug = re.sub(r"[ö]", "o", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] if slug else "untitled"


def _get_github_config(tenant_id: str) -> Optional[dict]:
    """Load github_integration from user_settings for a tenant."""
    try:
        sb = get_supabase()
        result = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        if result.data:
            return result.data.get("settings", {}).get("github_integration")
    except Exception as e:
        logger.error(f"_get_github_config error: {e}")
    return None


def _save_github_config(tenant_id: str, config: Optional[dict]):
    """Save github_integration into user_settings JSON."""
    try:
        sb = get_supabase()
        # Read current settings
        result = (
            sb.table("user_settings")
            .select("settings")
            .eq("user_id", tenant_id)
            .single()
            .execute()
        )
        current = result.data.get("settings", {}) if result.data else {}
        if config is None:
            current.pop("github_integration", None)
        else:
            current["github_integration"] = config

        sb.table("user_settings").upsert(
            {
                "user_id": tenant_id,
                "settings": current,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        logger.error(f"_save_github_config error: {e}")
        raise


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/connect")
async def connect_github(request: Request, payload: ConnectRequest):
    """Validate token and save GitHub connection config."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    # Validate token by calling GitHub API
    async with httpx.AsyncClient() as client:
        # If repo_owner and repo_name provided, validate repo access
        if payload.repo_owner and payload.repo_name:
            resp = await client.get(
                f"{GITHUB_API}/repos/{payload.repo_owner}/{payload.repo_name}",
                headers=_gh_headers(payload.github_token),
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="Ogiltig GitHub-token")
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"Repositoryt {payload.repo_owner}/{payload.repo_name} hittades inte eller saknar behörighet",
                )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="Kunde inte validera GitHub-anslutning")
        else:
            # Just validate the token
            resp = await client.get(
                f"{GITHUB_API}/user",
                headers=_gh_headers(payload.github_token),
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Ogiltig GitHub-token")

    config = {
        "github_token": payload.github_token,
        "repo_owner": payload.repo_owner or "",
        "repo_name": payload.repo_name or "",
        "blog_path": payload.blog_path,
        "branch": payload.branch,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_github_config(tenant_id, config)

    repo = f"{payload.repo_owner}/{payload.repo_name}" if payload.repo_owner else ""
    return {"connected": True, "repo": repo}


@router.get("/status")
async def github_status(request: Request):
    """Return connection status and config (masked token)."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = _get_github_config(tenant_id)

    if not config or not config.get("github_token"):
        return {"connected": False}

    return {
        "connected": True,
        "repo_owner": config.get("repo_owner", ""),
        "repo_name": config.get("repo_name", ""),
        "repo": f"{config.get('repo_owner', '')}/{config.get('repo_name', '')}",
        "blog_path": config.get("blog_path", "content/blog"),
        "branch": config.get("branch", "main"),
        "token_masked": _mask_token(config.get("github_token", "")),
        "connected_at": config.get("connected_at"),
    }


@router.post("/disconnect")
async def disconnect_github(request: Request):
    """Remove GitHub config from user_settings."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    _save_github_config(tenant_id, None)
    return {"connected": False, "message": "GitHub-koppling borttagen"}


@router.post("/publish")
async def publish_to_github(request: Request, payload: PublishRequest):
    """Create a PR with a markdown blog post in the connected GitHub repo."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = _get_github_config(tenant_id)

    if not config or not config.get("github_token"):
        raise HTTPException(status_code=400, detail="GitHub inte anslutet. Anslut i Installningar forst.")

    token = config["github_token"]
    owner = config["repo_owner"]
    repo = config["repo_name"]
    blog_path = config.get("blog_path", "content/blog")
    target_branch = config.get("branch", "main")

    if not owner or not repo:
        raise HTTPException(status_code=400, detail="Inget repository konfigurerat")

    # Resolve content
    title = payload.title
    body = payload.body
    content_id = payload.content_id

    if content_id and (not title or not body):
        try:
            sb = get_supabase()
            result = (
                sb.table("content_pieces")
                .select("*")
                .eq("id", content_id)
                .single()
                .execute()
            )
            if result.data:
                title = title or result.data.get("title", "Untitled")
                body = body or result.data.get("content", "")
            else:
                raise HTTPException(status_code=404, detail="Innehall hittades inte")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"publish fetch content error: {e}")
            raise HTTPException(status_code=500, detail="Kunde inte hamta innehall")

    if not title or not body:
        raise HTTPException(status_code=400, detail="Titel och innehall kravs")

    slug = payload.slug or _slugify(title)
    timestamp = int(time.time())
    new_branch = f"sama/blog/{slug}-{timestamp}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build markdown with frontmatter
    markdown = f"""---
title: "{title}"
date: "{today}"
author: "SAMA AI"
---

{body}
"""

    file_path = f"{blog_path.strip('/')}/{slug}.md"

    async with httpx.AsyncClient() as client:
        headers = _gh_headers(token)

        # 1. Get the SHA of the target branch
        ref_resp = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{target_branch}",
            headers=headers,
        )
        if ref_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Kunde inte hitta branchen '{target_branch}' i {owner}/{repo}",
            )
        base_sha = ref_resp.json()["object"]["sha"]

        # 2. Create a new branch
        create_ref_resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
            headers=headers,
            json={
                "ref": f"refs/heads/{new_branch}",
                "sha": base_sha,
            },
        )
        if create_ref_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Kunde inte skapa branch: {create_ref_resp.text[:200]}",
            )

        # 3. Create the file on the new branch
        encoded_content = b64encode(markdown.encode("utf-8")).decode("ascii")
        file_resp = await client.put(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{file_path}",
            headers=headers,
            json={
                "message": f"Add blog post: {title}",
                "content": encoded_content,
                "branch": new_branch,
            },
        )
        if file_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Kunde inte skapa filen: {file_resp.text[:200]}",
            )

        # 4. Create the Pull Request
        pr_resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=headers,
            json={
                "title": f"[SAMA] Nytt blogginlagg: {title}",
                "body": (
                    f"Automatiskt skapat av SAMA AI.\n\n"
                    f"**Titel:** {title}\n"
                    f"**Fil:** `{file_path}`\n"
                    f"**Datum:** {today}\n\n"
                    f"Granska och merga for att publicera."
                ),
                "head": new_branch,
                "base": target_branch,
            },
        )
        if pr_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Kunde inte skapa PR: {pr_resp.text[:200]}",
            )

        pr_data = pr_resp.json()
        pr_url = pr_data.get("html_url", "")

    # Update content_pieces status to published
    if content_id:
        try:
            sb = get_supabase()
            sb.table("content_pieces").update({"status": "published"}).eq("id", content_id).execute()
        except Exception as e:
            logger.warning(f"Could not update content status: {e}")

    return {
        "pr_url": pr_url,
        "branch": new_branch,
        "file_path": file_path,
    }


@router.get("/repos")
async def list_repos(request: Request):
    """List repos the user's token has access to."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    config = _get_github_config(tenant_id)

    if not config or not config.get("github_token"):
        raise HTTPException(status_code=400, detail="GitHub-token saknas")

    token = config["github_token"]
    repos = []

    async with httpx.AsyncClient() as client:
        headers = _gh_headers(token)
        # Fetch user repos (first 100, sorted by recently pushed)
        resp = await client.get(
            f"{GITHUB_API}/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member",
            headers=headers,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Kunde inte hamta repos")

        for r in resp.json():
            repos.append({
                "full_name": r["full_name"],
                "owner": r["owner"]["login"],
                "name": r["name"],
                "private": r["private"],
                "default_branch": r.get("default_branch", "main"),
            })

    return {"repos": repos}
