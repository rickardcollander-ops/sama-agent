"""
HTML/plaintext rendering for the weekly status email.

Uses inline-style tables for max compatibility across email clients (Gmail,
Outlook, Apple Mail). No template engine — just str.format with a single
top-level dict — keeps the dependency surface minimal.
"""

from html import escape
from typing import Iterable


# ── Section helpers ──────────────────────────────────────────────────────────


def _agent_block(agent_name: str, summary: str, highlights: Iterable[str]) -> str:
    items = [h for h in highlights if h]
    if not summary and not items:
        return ""
    pretty_name = {
        "seo": "SEO",
        "ads": "Ads",
        "content": "Content",
        "social": "Social",
        "reviews": "Reviews",
        "analytics": "Analytics",
        "ai_visibility": "AI-synlighet",
        "strategy": "Strategi",
        "geo": "Geo",
    }.get(agent_name.lower(), agent_name.title())

    bullets = "".join(
        f'<li style="margin:0 0 6px 0;color:#334155;font-size:14px;line-height:1.5">{escape(h)}</li>'
        for h in items[:5]
    )

    summary_html = (
        f'<p style="margin:0 0 8px 0;color:#475569;font-size:14px;line-height:1.5">{escape(summary)}</p>'
        if summary
        else ""
    )

    return f"""
    <tr>
      <td style="padding:18px 24px;border-bottom:1px solid #e2e8f0">
        <h3 style="margin:0 0 8px 0;color:#0f172a;font-size:15px;font-weight:600;letter-spacing:.01em">{escape(pretty_name)}</h3>
        {summary_html}
        {f'<ul style="margin:8px 0 0 18px;padding:0">{bullets}</ul>' if bullets else ""}
      </td>
    </tr>
    """


def _approvals_block(pending: list[dict], approvals_url: str) -> str:
    if not pending:
        return ""
    rows = "".join(
        f'<li style="margin:0 0 6px 0;color:#334155;font-size:14px;line-height:1.5">'
        f'{escape(p.get("title") or "Utan titel")}'
        f' <span style="color:#94a3b8">· {escape(p.get("content_type") or "innehåll")}</span>'
        f'</li>'
        for p in pending[:8]
    )
    extra = (
        f'<p style="margin:8px 0 0 0;color:#64748b;font-size:13px">+ {len(pending) - 8} fler</p>'
        if len(pending) > 8
        else ""
    )
    return f"""
    <tr>
      <td style="padding:18px 24px;background:#fffbeb;border-bottom:1px solid #fde68a">
        <h3 style="margin:0 0 8px 0;color:#92400e;font-size:15px;font-weight:600">Behöver din input</h3>
        <p style="margin:0 0 8px 0;color:#78350f;font-size:14px">
          {len(pending)} {"sak" if len(pending) == 1 else "saker"} väntar på ditt godkännande.
        </p>
        <ul style="margin:8px 0 0 18px;padding:0">{rows}</ul>
        {extra}
        <p style="margin:14px 0 0 0">
          <a href="{escape(approvals_url)}"
             style="display:inline-block;background:#f59e0b;color:#fff;text-decoration:none;
                    padding:9px 16px;border-radius:6px;font-size:14px;font-weight:600">
            Granska och godkänn →
          </a>
        </p>
      </td>
    </tr>
    """


def _problems_block(problems: list[str]) -> str:
    if not problems:
        return ""
    items = "".join(
        f'<li style="margin:0 0 6px 0;color:#7f1d1d;font-size:14px;line-height:1.5">{escape(p)}</li>'
        for p in problems[:5]
    )
    return f"""
    <tr>
      <td style="padding:18px 24px;background:#fef2f2;border-bottom:1px solid #fecaca">
        <h3 style="margin:0 0 8px 0;color:#991b1b;font-size:15px;font-weight:600">Problem att titta på</h3>
        <ul style="margin:0 0 0 18px;padding:0">{items}</ul>
      </td>
    </tr>
    """


# ── Top-level render ─────────────────────────────────────────────────────────


def render_weekly_status_html(
    *,
    brand_name: str,
    week_label: str,
    agent_sections: list[dict],
    pending_approvals: list[dict],
    problems: list[str],
    dashboard_url: str,
    approvals_url: str,
    unsubscribe_url: str,
    nothing_happened: bool,
) -> str:
    """Render the full HTML email."""
    agent_html = "".join(
        _agent_block(s["agent"], s.get("summary", ""), s.get("highlights", []))
        for s in agent_sections
    )

    if nothing_happened and not agent_html:
        agent_html = """
        <tr>
          <td style="padding:24px;text-align:center;color:#64748b;font-size:14px;line-height:1.6">
            Inget akut har hänt den här veckan — agenterna jobbar på i bakgrunden
            och hör av sig så fort något kräver din uppmärksamhet. 🌿
          </td>
        </tr>
        """

    title = brand_name or "Din vecka med Sama"

    return f"""<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{escape(title)} — veckostatus</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9">
    <tr>
      <td align="center" style="padding:32px 12px">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(15,23,42,0.06)">
          <!-- Header -->
          <tr>
            <td style="padding:28px 24px 16px 24px;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%)">
              <p style="margin:0;color:#94a3b8;font-size:12px;letter-spacing:.08em;text-transform:uppercase">Veckostatus · {escape(week_label)}</p>
              <h1 style="margin:6px 0 4px 0;color:#f8fafc;font-size:22px;font-weight:600">Det här har vi gjort åt dig</h1>
              <p style="margin:0;color:#cbd5e1;font-size:14px">{escape(title)}</p>
            </td>
          </tr>

          {_approvals_block(pending_approvals, approvals_url)}
          {_problems_block(problems)}
          {agent_html}

          <!-- Footer CTA -->
          <tr>
            <td style="padding:24px;text-align:center;background:#f8fafc;border-top:1px solid #e2e8f0">
              <a href="{escape(dashboard_url)}"
                 style="display:inline-block;background:#0f172a;color:#fff;text-decoration:none;
                        padding:11px 20px;border-radius:6px;font-size:14px;font-weight:600">
                Öppna dashboarden
              </a>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:18px 24px;text-align:center;color:#94a3b8;font-size:12px;line-height:1.6">
              Du får det här mailet eftersom veckorapporter är aktiverade i dina notisinställningar.<br />
              <a href="{escape(unsubscribe_url)}" style="color:#64748b;text-decoration:underline">Stäng av veckomail</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_weekly_status_text(
    *,
    brand_name: str,
    week_label: str,
    agent_sections: list[dict],
    pending_approvals: list[dict],
    problems: list[str],
    dashboard_url: str,
    approvals_url: str,
    nothing_happened: bool,
) -> str:
    """Plaintext fallback — same content, no markup."""
    lines: list[str] = []
    lines.append(f"{brand_name or 'Din vecka med Sama'} — veckostatus ({week_label})")
    lines.append("=" * 60)
    lines.append("")

    if pending_approvals:
        lines.append(f"Behöver din input ({len(pending_approvals)}):")
        for p in pending_approvals[:8]:
            lines.append(f"  - {p.get('title') or 'Utan titel'} ({p.get('content_type') or 'innehåll'})")
        lines.append(f"  Granska: {approvals_url}")
        lines.append("")

    if problems:
        lines.append("Problem att titta på:")
        for p in problems[:5]:
            lines.append(f"  - {p}")
        lines.append("")

    if nothing_happened and not agent_sections:
        lines.append("Inget akut har hänt den här veckan — agenterna jobbar på i bakgrunden.")
        lines.append("")
    else:
        for s in agent_sections:
            agent = s["agent"].upper()
            summary = s.get("summary", "")
            highlights = s.get("highlights", [])
            if not summary and not highlights:
                continue
            lines.append(f"[{agent}]")
            if summary:
                lines.append(f"  {summary}")
            for h in highlights[:5]:
                lines.append(f"  - {h}")
            lines.append("")

    lines.append(f"Öppna dashboarden: {dashboard_url}")
    return "\n".join(lines)
