"""
Audit + reconcile the content pipeline across every SAMA site (tenant).

For each tenant it reports the idea → draft → scheduled → published chain:
  * whether anything is planned ahead (scheduled_for >= today, Europe/Stockholm)
  * status histograms for content_plan_items and content_pieces
  * plan items whose linked piece is 'published' but that are still draft/scheduled
    (the stale state the old publish flow left behind)
  * plan items stuck in 'drafting' after a failed LLM run
  * approved/scheduled items past due but not yet published — the dashboard publish
    bridge ships these on its next 5-min tick (reported only)
  * plan items linked to a missing piece (orphans, reported only)

With --fix it repairs the two safe inconsistencies:
  1. sync plan items (and their pending_approvals) to 'published' when the linked
     content_piece is already published
  2. reset 'drafting' items older than --drafting-stale-hours back to 'idea' so the
     next autopilot run can pick them up again

--dry-run (default) only reports; nothing is mutated without --fix. Idempotent.

Usage:
    python scripts/audit_content_pipeline.py [--fix] [--tenant <id>] [--drafting-stale-hours 6]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Make `shared` importable no matter the working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("content_audit")

_STO = ZoneInfo("Europe/Stockholm")
PAGE = 1000

# Statuses that count as "real work sitting on the calendar ahead of now".
PLAN_FORWARD_STATUSES = {"idea", "draft", "scheduled", "approved"}


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _fetch_all(sb, table: str, columns: str, tenant: Optional[str]) -> List[Dict[str, Any]]:
    """Page through a table (optionally scoped to one tenant)."""
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


def _label_for(tenant_id: str, by_site_id: Dict[str, str], by_user_id: Dict[str, str]) -> str:
    """Content can be keyed by site_id or user_id — resolve a human label."""
    if tenant_id in by_site_id:
        return f"{by_site_id[tenant_id]} (site {tenant_id})"
    if tenant_id in by_user_id:
        return f"{by_user_id[tenant_id]} (user {tenant_id})"
    return f"(unlabelled {tenant_id})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="apply repairs (default: report only)")
    parser.add_argument("--dry-run", action="store_true", help="report only (the default; accepted for clarity)")
    parser.add_argument("--tenant", help="limit to a single tenant_id")
    parser.add_argument("--drafting-stale-hours", type=int, default=6)
    args = parser.parse_args()

    from shared.database import get_supabase

    sb = get_supabase()
    if sb is None:
        log.error("Supabase not configured (SUPABASE_URL / service role key missing)")
        return 2

    now_utc = datetime.now(timezone.utc)
    today_start = datetime.now(_STO).replace(hour=0, minute=0, second=0, microsecond=0)
    drafting_cutoff = now_utc - timedelta(hours=args.drafting_stale_hours)

    # ── Labels for the sites ────────────────────────────────────────────────
    by_site_id: Dict[str, str] = {}
    by_user_id: Dict[str, str] = {}
    try:
        for s in _fetch_all(sb, "user_sites", "id,user_id,site_name", None):
            name = s.get("site_name") or "site"
            if s.get("id"):
                by_site_id[str(s["id"])] = name
            if s.get("user_id"):
                by_user_id.setdefault(str(s["user_id"]), name)
    except Exception as e:
        log.warning("could not load user_sites labels: %s", e)

    # ── Pull the content tables once, group in Python ───────────────────────
    plan_items = _fetch_all(
        sb,
        "content_plan_items",
        "id,tenant_id,status,scheduled_for,content_piece_id,updated_at,created_at,auto_publish_on_schedule",
        args.tenant,
    )
    pieces = _fetch_all(sb, "content_pieces", "id,tenant_id,status,published_at", args.tenant)

    piece_by_id: Dict[str, Dict[str, Any]] = {str(p["id"]): p for p in pieces}

    # Every tenant that has content, plus every known site (so empty sites surface).
    tenant_ids = {str(r["tenant_id"]) for r in plan_items if r.get("tenant_id")}
    tenant_ids |= {str(p["tenant_id"]) for p in pieces if p.get("tenant_id")}
    if not args.tenant:
        tenant_ids |= set(by_site_id.keys())
    else:
        tenant_ids &= {args.tenant}

    plan_by_tenant: Dict[str, List[Dict[str, Any]]] = {}
    for r in plan_items:
        plan_by_tenant.setdefault(str(r.get("tenant_id")), []).append(r)
    pieces_by_tenant: Dict[str, List[Dict[str, Any]]] = {}
    for p in pieces:
        pieces_by_tenant.setdefault(str(p.get("tenant_id")), []).append(p)

    # Candidate counts (always tallied so --dry-run reports what --fix would do).
    totals = {"stale_published": 0, "stuck_drafting": 0}

    for tenant_id in sorted(tenant_ids):
        label = _label_for(tenant_id, by_site_id, by_user_id)
        plan = plan_by_tenant.get(tenant_id, [])
        tps = pieces_by_tenant.get(tenant_id, [])

        plan_status = Counter((r.get("status") or "?") for r in plan)
        piece_status = Counter((p.get("status") or "?") for p in tps)

        # Forward planning.
        forward = []
        for r in plan:
            if (r.get("status") in PLAN_FORWARD_STATUSES):
                sf = _parse_ts(r.get("scheduled_for"))
                if sf and sf >= today_start:
                    forward.append((sf, r))
        forward.sort(key=lambda x: x[0])

        # Published-consistency: piece is live but the plan row didn't follow.
        stale_published = [
            r for r in plan
            if r.get("content_piece_id")
            and (piece_by_id.get(str(r["content_piece_id"])) or {}).get("status") == "published"
            and r.get("status") != "published"
        ]

        # Orphans: plan row points at a piece that no longer exists.
        orphans = [
            r for r in plan
            if r.get("content_piece_id") and str(r["content_piece_id"]) not in piece_by_id
        ]

        # Stuck mid-draft.
        stuck_drafting = []
        for r in plan:
            if r.get("status") != "drafting":
                continue
            ts = _parse_ts(r.get("updated_at")) or _parse_ts(r.get("created_at"))
            if ts is None or ts < drafting_cutoff:
                stuck_drafting.append(r)

        # Past-due rows, split by whether the publish bridge will actually act:
        #   bridge_due  — linked piece is 'approved' + due → bridge ships next tick
        #   stale_backlog — past-due idea/draft that will NOT auto-publish (it was
        #                   scheduled for a date that came and went without action)
        bridge_due = []
        stale_backlog = []
        for r in plan:
            sf = _parse_ts(r.get("scheduled_for"))
            if not sf or sf > now_utc or r.get("status") == "published":
                continue
            piece = piece_by_id.get(str(r.get("content_piece_id"))) if r.get("content_piece_id") else None
            piece_st = (piece or {}).get("status")
            if piece_st == "published":
                continue
            if piece_st == "approved":
                bridge_due.append(r)
            else:
                stale_backlog.append(r)

        # ── Report ──────────────────────────────────────────────────────────
        log.info("──────────────────────────────────────────────────────────")
        log.info("SITE: %s", label)
        log.info("  plan items: %d  %s", len(plan), dict(plan_status))
        log.info("  pieces:     %d  %s", len(tps), dict(piece_status))
        if forward:
            preview = ", ".join(sf.astimezone(_STO).strftime("%Y-%m-%d") for sf, _ in forward[:7])
            log.info("  planned ahead: %d (next: %s)", len(forward), preview)
        else:
            log.info("  planned ahead: 0  ⚠ nothing scheduled for today or later")
        if stale_published:
            log.info("  ⚠ published pieces with non-published plan row: %d", len(stale_published))
        if stuck_drafting:
            log.info("  ⚠ stuck in 'drafting' > %dh: %d", args.drafting_stale_hours, len(stuck_drafting))
        if bridge_due:
            log.info("  · approved & due — bridge ships next tick: %d", len(bridge_due))
        if stale_backlog:
            log.info("  · past-due idea/draft (won't auto-publish, needs drafting/approval): %d", len(stale_backlog))
        if orphans:
            log.info("  · plan rows pointing at a missing piece: %d", len(orphans))

        totals["stale_published"] += len(stale_published)
        totals["stuck_drafting"] += len(stuck_drafting)

        # ── Fix ─────────────────────────────────────────────────────────────
        if not args.fix:
            continue

        if stale_published:
            ids = [r["id"] for r in stale_published]
            sb.table("content_plan_items").update({"status": "published"}).in_("id", ids).eq("tenant_id", tenant_id).execute()
            for r in stale_published:
                pid = str(r["content_piece_id"])
                published_at = (piece_by_id.get(pid) or {}).get("published_at") or now_utc.isoformat()
                try:
                    sb.table("pending_approvals").update(
                        {"status": "published", "published_at": published_at}
                    ).contains("metadata", {"piece_id": pid}).eq("tenant_id", tenant_id).execute()
                except Exception as e:
                    log.warning("  approval sync failed for piece %s: %s", pid, e)
            log.info("  ✔ synced %d plan item(s) to published", len(ids))

        if stuck_drafting:
            ids = [r["id"] for r in stuck_drafting]
            sb.table("content_plan_items").update({"status": "idea"}).in_("id", ids).eq("tenant_id", tenant_id).execute()
            log.info("  ✔ reset %d stuck 'drafting' item(s) to 'idea'", len(ids))

    log.info("══════════════════════════════════════════════════════════")
    verb = "fixed" if args.fix else "to fix"
    log.info(
        "done [%s]: sites=%d  stale_published(%s)=%d  stuck_drafting(%s)=%d",
        "FIX" if args.fix else "DRY-RUN (no changes)",
        len(tenant_ids), verb, totals["stale_published"], verb, totals["stuck_drafting"],
    )
    if not args.fix and (totals["stale_published"] or totals["stuck_drafting"]):
        log.info("re-run with --fix to apply the repairs above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
