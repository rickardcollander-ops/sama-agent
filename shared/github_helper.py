"""
GitHub Helper for creating files in repositories
Allows agents to create blog posts, pages, etc. directly in GitHub repos
"""

import base64
import httpx
from typing import Dict, Any, Optional
from shared.config import settings

import re as _re

GITHUB_API = "https://api.github.com"


def _markdown_to_jsx_sections(markdown: str, competitor: str) -> str:
    """Convert markdown content to JSX-safe HTML sections"""
    lines = markdown.split('\n')
    jsx_lines = []
    in_list = False
    in_table = False
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            if in_list:
                jsx_lines.append('          </ul>')
                in_list = False
            if in_table:
                jsx_lines.append('          </tbody></table>')
                in_table = False
            continue
        
        # Skip markdown title if it matches the page title
        if stripped.startswith('# ') and ('vs' in stripped.lower() or competitor.lower() in stripped.lower()):
            continue
        
        # Headers
        if stripped.startswith('#### '):
            if in_list:
                jsx_lines.append('          </ul>')
                in_list = False
            text = _escape_jsx(stripped[5:])
            jsx_lines.append(f'          <h4>{text}</h4>')
        elif stripped.startswith('### '):
            if in_list:
                jsx_lines.append('          </ul>')
                in_list = False
            text = _escape_jsx(stripped[4:])
            jsx_lines.append(f'          <h3>{text}</h3>')
        elif stripped.startswith('## '):
            if in_list:
                jsx_lines.append('          </ul>')
                in_list = False
            text = _escape_jsx(stripped[3:])
            jsx_lines.append(f'          <h2>{text}</h2>')
        # Table rows
        elif '|' in stripped and not stripped.startswith('*') and not stripped.startswith('-'):
            cells = [c.strip() for c in stripped.split('|') if c.strip()]
            if all(c.replace('-', '').replace(':', '') == '' for c in cells):
                continue  # Skip separator rows
            if not in_table:
                jsx_lines.append('          <table>')
                jsx_lines.append('          <thead><tr>')
                for cell in cells:
                    jsx_lines.append(f'            <th>{_escape_jsx(_clean_md(cell))}</th>')
                jsx_lines.append('          </tr></thead>')
                jsx_lines.append('          <tbody>')
                in_table = True
            else:
                jsx_lines.append('          <tr>')
                for cell in cells:
                    jsx_lines.append(f'            <td>{_escape_jsx(_clean_md(cell))}</td>')
                jsx_lines.append('          </tr>')
        # List items
        elif stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                jsx_lines.append('          <ul>')
                in_list = True
            text = _escape_jsx(_clean_md(stripped[2:]))
            jsx_lines.append(f'            <li>{text}</li>')
        # Regular paragraphs
        else:
            if in_list:
                jsx_lines.append('          </ul>')
                in_list = False
            text = _escape_jsx(_clean_md(stripped))
            if text:
                jsx_lines.append(f'          <p>{text}</p>')
    
    if in_list:
        jsx_lines.append('          </ul>')
    if in_table:
        jsx_lines.append('          </tbody></table>')
    
    return '\n'.join(jsx_lines)


def _clean_md(text: str) -> str:
    """Remove markdown formatting and convert to plain text with HTML bold/italic"""
    # Bold
    text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Checkmarks
    text = text.replace('✅', '✅ ').replace('❌', '❌ ')
    return text


def _escape_jsx(text: str) -> str:
    """Escape characters that are problematic in JSX"""
    text = text.replace('{', '&#123;').replace('}', '&#125;')
    return text


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
    
    # Convert markdown content to JSX sections
    sections = _markdown_to_jsx_sections(content, competitor)
    
    # Create Next.js page component
    page_content = f"""import {{ Metadata }} from "next";

export const metadata: Metadata = {{
  title: "Successifier vs {competitor.title()} - Customer Success Platform Comparison",
  description: "Compare Successifier and {competitor.title()}. See features, pricing, and why growing SaaS teams choose Successifier.",
}};

export default function {competitor.title().replace(' ', '')}ComparisonPage() {{
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <div className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
        <div className="mb-8 text-center">
          <p className="text-sm font-semibold text-blue-600 uppercase tracking-wide">Comparison</p>
          <h1 className="mt-2 text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl">
            Successifier vs {competitor.title()}
          </h1>
          <p className="mt-4 text-xl text-slate-600 max-w-2xl mx-auto">
            Which customer success platform is right for your team?
          </p>
        </div>

        <article className="prose prose-slate prose-lg max-w-none prose-headings:font-bold prose-h2:text-2xl prose-h2:mt-12 prose-h2:mb-4 prose-h3:text-xl prose-h3:mt-8 prose-h4:text-lg prose-p:text-slate-700 prose-p:leading-relaxed prose-li:text-slate-700 prose-strong:text-slate-900 prose-table:border-collapse prose-th:bg-slate-100 prose-th:p-3 prose-th:text-left prose-th:border prose-th:border-slate-200 prose-td:p-3 prose-td:border prose-td:border-slate-200">
{sections}
        </article>

        <div className="mt-16 rounded-2xl bg-blue-600 p-8 text-center text-white">
          <h2 className="text-2xl font-bold">Ready to try Successifier?</h2>
          <p className="mt-2 text-blue-100">Start your free trial today. No credit card required.</p>
          <a href="https://successifier.com/pricing" className="mt-6 inline-block rounded-lg bg-white px-8 py-3 font-semibold text-blue-600 hover:bg-blue-50 transition-colors">
            Get Started Free
          </a>
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
