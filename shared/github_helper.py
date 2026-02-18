"""
GitHub Helper for creating files in repositories
Allows agents to create blog posts, pages, etc. directly in GitHub repos
"""

import base64
import httpx
from typing import Dict, Any, Optional
from shared.config import settings

GITHUB_API = "https://api.github.com"


async def create_or_update_file(
    repo_owner: str,
    repo_name: str,
    file_path: str,
    content: str,
    commit_message: str,
    branch: str = "master",
    github_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create or update a file in a GitHub repository
    
    Args:
        repo_owner: GitHub username/org (e.g., 'rickardcollander-ops')
        repo_name: Repository name (e.g., 'successifier-homepage')
        file_path: Path to file in repo (e.g., 'content/blog/my-post.md')
        content: File content as string
        commit_message: Git commit message
        branch: Branch name (default: 'master')
        github_token: GitHub personal access token
    
    Returns:
        Response from GitHub API
    """
    token = github_token or getattr(settings, 'GITHUB_TOKEN', '')
    
    if not token:
        return {"error": "GITHUB_TOKEN not configured", "success": False}
    
    # Encode content to base64
    content_bytes = content.encode('utf-8')
    content_base64 = base64.b64encode(content_bytes).decode('utf-8')
    
    # Check if file exists to get SHA (required for updates)
    url = f"{GITHUB_API}/repos/{repo_owner}/{repo_name}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    sha = None
    async with httpx.AsyncClient() as client:
        # Try to get existing file
        try:
            resp = await client.get(url, headers=headers, params={"ref": branch})
            if resp.status_code == 200:
                sha = resp.json().get("sha")
        except Exception:
            pass
        
        # Create or update file
        payload = {
            "message": commit_message,
            "content": content_base64,
            "branch": branch
        }
        
        if sha:
            payload["sha"] = sha
        
        resp = await client.put(url, headers=headers, json=payload)
        
        if resp.status_code in (200, 201):
            return {
                "success": True,
                "file_path": file_path,
                "commit": resp.json().get("commit", {}),
                "content_url": resp.json().get("content", {}).get("html_url", "")
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {resp.status_code}",
                "message": resp.text
            }


async def create_blog_post(
    title: str,
    content: str,
    slug: str,
    excerpt: str,
    keywords: list,
    meta_description: str,
    author: str = "SAMA Agent",
    repo_owner: str = "rickardcollander-ops",
    repo_name: str = "successifier-homepage"
) -> Dict[str, Any]:
    """
    Create a blog post in the Successifier homepage repo
    
    Args:
        title: Blog post title
        content: Blog post content (markdown)
        slug: URL slug (e.g., 'customer-success-metrics')
        excerpt: Short excerpt
        keywords: List of keywords
        meta_description: SEO meta description
        author: Post author
        repo_owner: GitHub repo owner
        repo_name: GitHub repo name
    
    Returns:
        Result of file creation
    """
    from datetime import datetime
    
    # Create frontmatter
    frontmatter = f"""---
title: "{title}"
date: "{datetime.utcnow().isoformat()}"
excerpt: "{excerpt}"
author: "{author}"
keywords: {keywords}
metaDescription: "{meta_description}"
readingTime: {max(1, len(content.split()) // 200)}
---

"""
    
    full_content = frontmatter + content
    file_path = f"content/blog/{slug}.md"
    commit_message = f"Add blog post: {title}"
    
    return await create_or_update_file(
        repo_owner=repo_owner,
        repo_name=repo_name,
        file_path=file_path,
        content=full_content,
        commit_message=commit_message
    )


async def create_comparison_page(
    competitor: str,
    content: str,
    repo_owner: str = "rickardcollander-ops",
    repo_name: str = "successifier-homepage"
) -> Dict[str, Any]:
    """
    Create a competitor comparison page
    
    Args:
        competitor: Competitor name (e.g., 'gainsight')
        content: Page content (markdown)
        repo_owner: GitHub repo owner
        repo_name: GitHub repo name
    
    Returns:
        Result of file creation
    """
    file_path = f"app/vs/{competitor}/page.tsx"
    
    # Escape content for JSX (replace backticks and handle quotes)
    content_escaped = content.replace('`', '\\`').replace('${', '\\${')
    
    # Create Next.js page component
    page_content = f"""export default function {competitor.title()}ComparisonPage() {{
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <div className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
        <h1 className="text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl mb-8">
          Successifier vs {competitor.title()}
        </h1>
        <div className="prose prose-slate max-w-none">
          <div dangerouslySetInnerHTML={{{{ __html: `{content_escaped}` }}}} />
        </div>
      </div>
    </div>
  );
}}
"""
    
    commit_message = f"Add comparison page: Successifier vs {competitor.title()}"
    
    return await create_or_update_file(
        repo_owner=repo_owner,
        repo_name=repo_name,
        file_path=file_path,
        content=page_content,
        commit_message=commit_message
    )
