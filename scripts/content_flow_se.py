"""
Reset + refill the content pipeline for a single tenant.

Built for successifier.se after the publish-bridge bug (the dashboard sent a
bare legacy X-Tenant-ID to the protected /api/content/* routes, so the backend
returned an empty calendar and nothing ever published — drafts and approvals
piled up but never shipped). This tool clears that dead backlog and refills the
calendar going forward.

Two independent phases, both opt-in. Nothing is written without --apply
(dry-run is the default), and the tool is idempotent.

  --archive-backlog
      Archive the stale backlog the bug left behind: plan items whose
      scheduled_for is in the PAST and whose linked content_piece is NOT
      published. Sets plan item + piece status to 'archived' and any matching
      pending_approvals to 'rejected'. This is reversible — there is no hard
      delete, and *published* rows are never touched, so already-live pages on
      the site are unaffected.
      Add --include-future to also archive not-yet-due unpublished items
      (ideas/drafts/approved with a future or absent scheduled_for) — i.e. a
      full queue reset rather than just the past-due junk.

  --fill-forward N
      Generate + draft one article for each of the next N days (Europe/
      Stockholm) that currently has nothing scheduled, pinning each to its
      calendar date. Gap-fill: a day that already has a planned or published
      item is skipped. Runs the same autopilot pipeline the daily cron uses.
      Honours --auto-publish / --min-score for the publish mode of the drafts
      it creates.

The tenant is identified by --tenant <id> (this is the site_id that content is
keyed under) OR by --domain <substring> which resolves the site_id from
user_sites (matching settings->domain / blog_url / site_name).

Examples:
    # Dry-run report for successifier (no writes):
    python scripts/content_flow_se.py --domain successifier.se

    # Clear the dead past-due backlog, then fill the next 14 days, for real:
    python scripts/content_flow_se.py --domain successifier.se \
        --archive-backlog --fill-forward 14 --apply

    # Full queue reset (also drop future unpublished) + refill 21 days:
    python scripts/content_flow_se.py --tenant <site_id> \
        --archive-backlog --include-future --fill-forward 21 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Make `shared` / `api` importable no matter the working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("content_flow")

_STO = ZoneInfo("Europe/Stockholm")
PAGE = 1000

# Plan statuses that represent "real work" (everything except terminal states).
UNPUBLISHED_PLAN_STATUSES = {"idea", "drafting", "draft", "scheduled", "approved"}


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _fetch_all(sb, table: str, columns: str, tenant: Optional[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        q = sb.table(table).select(columns)
        if tenant:
            q = q.eq("tenant_id", tenant)
        res = q.range(offset, offset + PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def _resolve_tenant(sb, tenant: Optional[str], domain: Optional[str]) -> Optional[str]:
    """Resolve the tenant_id (site_id) content is keyed under.

    --tenant wins. Otherwise match --domain against the site's
    settings.domain / settings.blog_url / site_name.
    """
    if tenant:
        return tenant
    if not domain:
        return None
    needle = domain.strip().lower()
    candidates: List[tuple[str, str]] = []
    try:
        sites = _fetch_all(sb, "user_sites", "id,user_id,site_name,settings", None)
    except Exception as e:
        log.error("could not load user_sites to resolve --domain: %s", e)
        return None
    for s in sites:
        settings = s.get("settings") or {}
        hay = " ".join(
            str(v or "").lower()
            for v in (
                settings.get("domain"),
                settings.get("blog_url"),
                s.get("site_name"),
            )
        )
        if needle in hay:
            candidates.append((str(s["id"]), s.get("site_name") or settings.get("domain") or "site"))
    if not candidates:
        log.error("no site matched --domain %r", domain)
        return None
    if len(candidates) > 1:
        log.error("--domain %r is ambiguous, matched %d sites: %s — use --tenant",
                  domain, len(candidates), candidates)
        return None
    site_id, name = candidates[0]
    log.info("resolved --domain %r -> site_id %s (%s)", domain, site_id, name)
    return site_id


# ── Phase 1: archive backlog ────────────────────────────────────────────────

def archive_backlog(sb, tenant_id: str, include_future: bool, apply: bool) -> Dict[str, int]:
    now_utc = datetime.now(timezone.utc)
    plan = _fetch_all(
        sb, "content_plan_items",
        "id,tenant_id,status,scheduled_for,content_piece_id", tenant_id,
    )
    pieces = _fetch_all(sb, "content_pieces", "id,tenant_id,status", tenant_id)
    piece_by_id = {str(p["id"]): p for p in pieces}

    targets: List[Dict[str, Any]] = []
    for r in plan:
        st = r.get("status")
        if st not in UNPUBLISHED_PLAN_STATUSES:
            continue  # already published/archived — never touched
        piece = piece_by_id.get(str(r.get("content_piece_id"))) if r.get("content_piece_id") else None
        if (piece or {}).get("status") == "published":
            continue  # linked piece is live — leave it alone
        sf = _parse_ts(r.get("scheduled_for"))
        is_past_due = sf is not None and sf < now_utc
        if include_future or is_past_due:
            targets.append(r)

    plan_ids = [r["id"] for r in targets]
    piece_ids = [
        str(r["content_piece_id"]) for r in targets
        if r.get("content_piece_id")
        and (piece_by_id.get(str(r["content_piece_id"])) or {}).get("status") not in (None, "published", "archived")
    ]

    scope = "all unpublished" if include_future else "past-due unpublished"
    log.info("archive-backlog [%s]: %d plan item(s), %d piece(s) to archive",
             scope, len(plan_ids), len(piece_ids))
    if not apply:
        if plan_ids:
            log.info("  (dry-run — re-run with --apply to archive)")
        return {"plan_archived": len(plan_ids), "pieces_archived": len(piece_ids)}

    # Batch the updates so we don't issue one round-trip per row.
    for i in range(0, len(plan_ids), 200):
        chunk = plan_ids[i:i + 200]
        sb.table("content_plan_items").update({"status": "archived"}).in_("id", chunk).eq("tenant_id", tenant_id).execute()
    for i in range(0, len(piece_ids), 200):
        chunk = piece_ids[i:i + 200]
        sb.table("content_pieces").update({"status": "archived"}).in_("id", chunk).eq("tenant_id", tenant_id).execute()
    # Drop the matching approvals out of the pending queue (reversible: 'rejected',
    # not deleted). pending_approvals has no 'archived' status.
    for pid in piece_ids:
        try:
            sb.table("pending_approvals").update({"status": "rejected"}).contains(
                "metadata", {"piece_id": pid}
            ).eq("tenant_id", tenant_id).neq("status", "published").execute()
        except Exception as e:
            log.warning("  approval cleanup failed for piece %s: %s", pid, e)

    log.info("  ✔ archived %d plan item(s) and %d piece(s)", len(plan_ids), len(piece_ids))
    return {"plan_archived": len(plan_ids), "pieces_archived": len(piece_ids)}


# ── Phase 2: fill forward ───────────────────────────────────────────────────

def _empty_days_ahead(sb, tenant_id: str, n: int) -> List[int]:
    """Return the day-offsets (1..n) whose Europe/Stockholm calendar day has no
    non-archived plan item scheduled — the gaps the fill should target."""
    empty: List[int] = []
    for d in range(1, n + 1):
        target = (datetime.now(_STO) + timedelta(days=d)).date()
        day_start = datetime(target.year, target.month, target.day, tzinfo=_STO).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)
        res = (
            sb.table("content_plan_items")
            .select("id")
            .eq("tenant_id", tenant_id)
            .gte("scheduled_for", day_start.isoformat())
            .lt("scheduled_for", day_end.isoformat())
            .in_("status", ["idea", "drafting", "draft", "scheduled", "approved", "published"])
            .limit(1)
            .execute()
        )
        if not (res.data or []):
            empty.append(d)
    return empty


async def fill_forward(sb, tenant_id: str, n: int, auto_publish: bool,
                       min_score: int, apply: bool) -> Dict[str, int]:
    empty = _empty_days_ahead(sb, tenant_id, n)
    filled_dates = [
        (datetime.now(_STO) + timedelta(days=d)).date().isoformat() for d in empty
    ]
    log.info("fill-forward: %d of next %d day(s) are empty: %s",
             len(empty), n, ", ".join(filled_dates) or "(none — calendar already full)")
    if not apply:
        if empty:
            log.info("  (dry-run — re-run with --apply to generate)")
        return {"days_targeted": len(empty), "days_generated": 0}

    from shared.scheduler import _run_content_autopilot_for_tenant

    generated = 0
    for d in empty:
        cfg = {
            "source": "daily_cron",          # enables the per-day gap-fill guard
            "ideas_per_run": 1,
            "auto_draft_top_n": 1,
            "auto_publish": auto_publish,
            "min_score_for_publish": min_score,
            "scheduled_for_days_ahead": d,
        }
        date_str = (datetime.now(_STO) + timedelta(days=d)).date().isoformat()
        try:
            res = await _run_content_autopilot_for_tenant(tenant_id, cfg)
            if res.get("skipped"):
                log.info("  · %s: skipped (%s)", date_str, res.get("reason"))
            else:
                generated += 1
                log.info("  ✔ %s: drafted=%s queued=%s scheduled=%s",
                         date_str, res.get("drafted"), res.get("queued"), res.get("scheduled"))
        except Exception as e:
            log.warning("  ✗ %s: generation failed: %s", date_str, e)
    log.info("  ✔ generated content for %d day(s)", generated)
    return {"days_targeted": len(empty), "days_generated": generated}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant", help="tenant_id (site_id) content is keyed under")
    p.add_argument("--domain", help="resolve the site_id from this domain/name substring")
    p.add_argument("--archive-backlog", action="store_true", help="archive stale unpublished plan items + pieces")
    p.add_argument("--include-future", action="store_true", help="with --archive-backlog: also archive not-yet-due unpublished items (full queue reset)")
    p.add_argument("--fill-forward", type=int, metavar="N", default=0, help="generate one article for each empty day in the next N days")
    p.add_argument("--auto-publish", action="store_true", help="drafts created by --fill-forward publish automatically when score >= min-score")
    p.add_argument("--min-score", type=int, default=70, help="score threshold for --auto-publish (default 70)")
    p.add_argument("--apply", action="store_true", help="actually write changes (default: dry-run)")
    args = p.parse_args()

    if not args.archive_backlog and not args.fill_forward:
        log.error("nothing to do — pass --archive-backlog and/or --fill-forward N")
        return 2

    from shared.database import get_supabase
    sb = get_supabase()
    if sb is None:
        log.error("Supabase not configured (SUPABASE_URL / service role key missing)")
        return 2

    tenant_id = _resolve_tenant(sb, args.tenant, args.domain)
    if not tenant_id:
        log.error("could not resolve a tenant — pass --tenant <site_id> or a unique --domain")
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN (no changes)"
    log.info("══════════════════════════════════════════════════════════")
    log.info("content flow — tenant=%s  [%s]", tenant_id, mode)
    log.info("══════════════════════════════════════════════════════════")

    if args.archive_backlog:
        archive_backlog(sb, tenant_id, args.include_future, args.apply)

    if args.fill_forward:
        asyncio.run(fill_forward(sb, tenant_id, args.fill_forward, args.auto_publish, args.min_score, args.apply))

    log.info("══════════════════════════════════════════════════════════")
    if not args.apply:
        log.info("dry-run complete — re-run with --apply to make the changes above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
