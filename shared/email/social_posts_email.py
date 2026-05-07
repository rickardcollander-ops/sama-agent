"""
Social-posts email -- the day after an article publishes, mail the user
all the social-post copy (LinkedIn / X / Instagram / Facebook) that was
generated alongside the article so they can publish manually on each
platform. The article link is filled into every {{ARTICLE_URL}}
placeholder before sending.

Entry point: send_social_posts_email(article_content_id, recipient_email)
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.brevo_client import send_transactional_email
from shared.database import get_supabase

logger = logging.getLogger(__name__)


_PLATFORM_LABELS = {
    "social_linkedin": ("LinkedIn", "#0A66C2"),
    "social_x": ("X (Twitter)", "#000000"),
    "social_instagram": ("Instagram", "#E1306C"),
    "social_facebook": ("Facebook", "#1877F2"),
}


def _render_html(
    article_title: str,
    article_url: str,
    social_pieces: List[Dict[str, Any]],
) -> str:
    blocks: List[str] = []
    for sp in social_pieces:
        ctype = sp.get("content_type") or ""
        label, color = _PLATFORM_LABELS.get(ctype, (ctype, "#475569"))
        body = (sp.get("content") or "").replace("{{ARTICLE_URL}}", article_url)
        body_html = html.escape(body).replace("\n", "<br>")
        blocks.append(f"""
<div style="border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin:16px 0;background:#fff;">
  <div style="display:inline-block;padding:4px 10px;border-radius:999px;background:{color};color:#fff;font-size:12px;font-weight:600;letter-spacing:0.3px;text-transform:uppercase;">{html.escape(label)}</div>
  <div style="margin-top:12px;font-size:14px;line-height:1.55;color:#0f172a;white-space:pre-wrap;">{body_html}</div>
  <div style="margin-top:12px;font-size:12px;color:#64748b;">Tip: copy the text above and paste it directly into {html.escape(label)}.</div>
</div>
""")
    blocks_html = "\n".join(blocks)

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#0f172a;">
  <div style="max-width:640px;margin:0 auto;padding:32px 24px;">
    <h1 style="font-size:22px;font-weight:700;margin:0 0 8px;">Your social posts are ready to publish</h1>
    <p style="margin:0 0 24px;color:#475569;font-size:14px;line-height:1.55;">
      Yesterday's article <a href="{html.escape(article_url)}" style="color:#2563eb;text-decoration:none;"><strong>{html.escape(article_title)}</strong></a> went live. Below are the matching posts for each platform you selected. Copy each one and post it manually so the publishing schedule stays in your hands.
    </p>
    {blocks_html}
    <p style="margin-top:32px;font-size:12px;color:#94a3b8;">Sent by SAMA. Article: <a href="{html.escape(article_url)}" style="color:#94a3b8;">{html.escape(article_url)}</a></p>
  </div>
</body></html>
"""


async def send_social_posts_email(
    article_content_id: str,
    *,
    recipient_email: Optional[str] = None,
) -> Dict[str, Any]:
    """Render + send the social-posts email for one published article.

    Returns {sent: bool, social_count: int, error?: str}.
    """
    sb = get_supabase()

    # 1. Load the parent article
    try:
        parent = (
            sb.table("content_pieces")
            .select("id,tenant_id,title,target_url,external_url,published_at")
            .eq("id", article_content_id)
            .single()
            .execute()
        )
    except Exception as e:
        return {"sent": False, "error": f"parent lookup failed: {e}", "social_count": 0}
    if not parent.data:
        return {"sent": False, "error": "parent article not found", "social_count": 0}

    article = parent.data
    tenant_id = article.get("tenant_id")
    article_url = article.get("external_url") or article.get("target_url") or ""
    if not article_url:
        return {"sent": False, "error": "article has no published URL yet", "social_count": 0}

    # 2. Load all social children
    try:
        kids = (
            sb.table("content_pieces")
            .select("id,content_type,content,title")
            .eq("parent_content_id", article_content_id)
            .execute()
        )
    except Exception as e:
        return {"sent": False, "error": f"children lookup failed: {e}", "social_count": 0}

    social_pieces = [
        c for c in (kids.data or [])
        if (c.get("content_type") or "").startswith("social_")
    ]
    if not social_pieces:
        return {"sent": False, "error": "no social children", "social_count": 0}

    # 3. Resolve recipient email (passed in or via tenant config)
    if not recipient_email and tenant_id:
        try:
            site = (
                sb.table("user_sites")
                .select("settings")
                .eq("id", tenant_id)
                .single()
                .execute()
            )
            if site.data and isinstance(site.data.get("settings"), dict):
                recipient_email = site.data["settings"].get("contact_email") or \
                                  site.data["settings"].get("email")
        except Exception:
            pass
    if not recipient_email:
        return {"sent": False, "error": "no recipient email configured", "social_count": len(social_pieces)}

    # 4. Render + send
    html_body = _render_html(
        article_title=article.get("title") or "your article",
        article_url=article_url,
        social_pieces=social_pieces,
    )
    subject = f"Social posts ready: {article.get('title') or 'your article'}"

    try:
        ok = await send_transactional_email(
            to_email=recipient_email,
            subject=subject,
            html_body=html_body,
        )
    except Exception as e:
        return {"sent": False, "error": str(e), "social_count": len(social_pieces)}

    if not ok:
        return {"sent": False, "error": "brevo send returned false", "social_count": len(social_pieces)}

    # 5. Persist substituted copy back so the dashboard shows the real URL
    now_iso = datetime.now(timezone.utc).isoformat()
    for sp in social_pieces:
        try:
            sb.table("content_pieces").update({
                "content": (sp.get("content") or "").replace("{{ARTICLE_URL}}", article_url),
            }).eq("id", sp["id"]).execute()
        except Exception:
            pass

    # 6. Mark plan_items emailed
    try:
        sb.table("content_plan_items").update({"emailed_at": now_iso}).in_(
            "content_piece_id", [sp["id"] for sp in social_pieces]
        ).execute()
    except Exception as e:
        logger.debug(f"could not mark plan_items emailed_at: {e}")

    return {"sent": True, "social_count": len(social_pieces)}
