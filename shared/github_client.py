"""
GitHub API client for FORGE — read access to repos, commits, PRs, issues, deployments.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

import httpx

from shared.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"


def _headers() -> Dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    return h


def _repos() -> List[str]:
    return [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]


async def _get(path: str, params: Optional[Dict] = None) -> Any:
    """Make a GET request to GitHub API."""
    async with httpx.AsyncClient(base_url=BASE_URL, headers=_headers(), timeout=15.0) as client:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


async def get_recent_commits(repo: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent commits across repos."""
    repos = [repo] if repo else _repos()
    owner = settings.GITHUB_OWNER
    all_commits = []

    for r in repos:
        try:
            commits = await _get(f"/repos/{owner}/{r}/commits", {"per_page": limit})
            for c in commits:
                all_commits.append({
                    "repo": r,
                    "sha": c["sha"][:7],
                    "message": c["commit"]["message"].split("\n")[0][:120],
                    "author": c["commit"]["author"]["name"],
                    "date": c["commit"]["author"]["date"],
                })
        except Exception as e:
            logger.debug(f"[github] Could not fetch commits for {owner}/{r}: {e}")

    all_commits.sort(key=lambda x: x["date"], reverse=True)
    return all_commits[:limit]


async def get_open_prs(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get open pull requests."""
    repos = [repo] if repo else _repos()
    owner = settings.GITHUB_OWNER
    all_prs = []

    for r in repos:
        try:
            prs = await _get(f"/repos/{owner}/{r}/pulls", {"state": "open", "per_page": 20})
            for pr in prs:
                all_prs.append({
                    "repo": r,
                    "number": pr["number"],
                    "title": pr["title"][:100],
                    "author": pr["user"]["login"],
                    "branch": pr["head"]["ref"],
                    "created_at": pr["created_at"],
                    "updated_at": pr["updated_at"],
                    "draft": pr.get("draft", False),
                    "url": pr["html_url"],
                })
        except Exception as e:
            logger.debug(f"[github] Could not fetch PRs for {owner}/{r}: {e}")

    return all_prs


async def get_open_issues(repo: Optional[str] = None, limit: int = 15) -> List[Dict[str, Any]]:
    """Get open issues (excluding PRs)."""
    repos = [repo] if repo else _repos()
    owner = settings.GITHUB_OWNER
    all_issues = []

    for r in repos:
        try:
            issues = await _get(f"/repos/{owner}/{r}/issues", {
                "state": "open",
                "per_page": limit,
                "sort": "updated",
            })
            for i in issues:
                if "pull_request" in i:
                    continue  # Skip PRs
                all_issues.append({
                    "repo": r,
                    "number": i["number"],
                    "title": i["title"][:100],
                    "labels": [l["name"] for l in i.get("labels", [])],
                    "author": i["user"]["login"],
                    "created_at": i["created_at"],
                    "url": i["html_url"],
                })
        except Exception as e:
            logger.debug(f"[github] Could not fetch issues for {owner}/{r}: {e}")

    return all_issues[:limit]


async def get_recent_deployments(repo: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
    """Get recent deployments from GitHub."""
    repos = [repo] if repo else _repos()
    owner = settings.GITHUB_OWNER
    all_deploys = []

    for r in repos:
        try:
            deployments = await _get(f"/repos/{owner}/{r}/deployments", {"per_page": limit})
            for d in deployments:
                # Get status
                statuses = await _get(f"/repos/{owner}/{r}/deployments/{d['id']}/statuses", {"per_page": 1})
                status = statuses[0]["state"] if statuses else "unknown"

                all_deploys.append({
                    "repo": r,
                    "environment": d.get("environment", "?"),
                    "ref": d.get("ref", "?"),
                    "status": status,
                    "created_at": d["created_at"],
                    "creator": d["creator"]["login"] if d.get("creator") else "?",
                })
        except Exception as e:
            logger.debug(f"[github] Could not fetch deployments for {owner}/{r}: {e}")

    return all_deploys


async def get_repo_summary() -> Dict[str, Any]:
    """Get a summary of all repos — for FORGE's context."""
    owner = settings.GITHUB_OWNER
    repos = _repos()
    summary = {"owner": owner, "repos": []}

    for r in repos:
        try:
            info = await _get(f"/repos/{owner}/{r}")
            summary["repos"].append({
                "name": r,
                "default_branch": info.get("default_branch", "main"),
                "open_issues": info.get("open_issues_count", 0),
                "updated_at": info.get("pushed_at", "?"),
                "language": info.get("language", "?"),
            })
        except Exception as e:
            summary["repos"].append({"name": r, "error": str(e)[:100]})

    return summary


async def get_forge_github_context() -> str:
    """Build a GitHub context string for FORGE's chat context."""
    if not settings.GITHUB_TOKEN:
        return "(GitHub-access ej konfigurerad — sätt GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPOS)"

    parts = []

    try:
        commits = await get_recent_commits(limit=8)
        if commits:
            lines = [f"  - [{c['repo']}] {c['sha']} {c['message']} ({c['author']}, {c['date'][:10]})" for c in commits]
            parts.append("SENASTE COMMITS:\n" + "\n".join(lines))
    except Exception:
        pass

    try:
        prs = await get_open_prs()
        if prs:
            lines = []
            for pr in prs:
                draft = " [DRAFT]" if pr["draft"] else ""
                lines.append(f"  - [{pr['repo']}] #{pr['number']} {pr['title']}{draft} (av {pr['author']}, branch: {pr['branch']})")
            parts.append(f"ÖPPNA PR:s ({len(prs)}):\n" + "\n".join(lines))
        else:
            parts.append("ÖPPNA PR:s: inga")
    except Exception:
        pass

    try:
        issues = await get_open_issues(limit=10)
        if issues:
            lines = []
            for i in issues:
                labels = f" [{', '.join(i['labels'])}]" if i["labels"] else ""
                lines.append(f"  - [{i['repo']}] #{i['number']} {i['title']}{labels}")
            parts.append(f"ÖPPNA ISSUES ({len(issues)}):\n" + "\n".join(lines))
    except Exception:
        pass

    try:
        deploys = await get_recent_deployments(limit=3)
        if deploys:
            lines = [f"  - [{d['repo']}] {d['environment']}: {d['status']} ({d['ref']}, {d['created_at'][:10]})" for d in deploys]
            parts.append("SENASTE DEPLOYS:\n" + "\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "(Kunde inte hämta GitHub-data)"
