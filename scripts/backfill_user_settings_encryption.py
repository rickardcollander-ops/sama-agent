"""
Backfill: encrypt existing user_settings rows.

Reads every row from ``user_settings``, splits out secret-shaped fields, and
writes them into the new ``settings_encrypted`` column. The plaintext
``settings`` column is rewritten to drop secret fields so the migration is
idempotent (re-runs are safe).

Usage:
    MASTER_KMS_KEY=<base64> python scripts/backfill_user_settings_encryption.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    from shared.database import get_supabase
    from shared.secrets_vault import (
        encrypt_payload,
        encryption_available,
        split_secrets,
    )

    if not encryption_available():
        log.error("MASTER_KMS_KEY not configured; refusing to run")
        return 2

    sb = get_supabase()
    if sb is None:
        log.error("Supabase not configured")
        return 2

    offset = 0
    total = 0
    encrypted = 0
    skipped = 0

    while True:
        res = (
            sb.table("user_settings")
            .select("user_id, settings, settings_encrypted")
            .range(offset, offset + args.batch_size - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break

        for row in rows:
            total += 1
            user_id = row["user_id"]
            current = row.get("settings") or {}
            non_secrets, secrets = split_secrets(current)

            if not secrets:
                skipped += 1
                continue

            blob = encrypt_payload(secrets)
            if blob is None:
                log.warning("encrypt failed for user_id=%s; skipping", user_id)
                skipped += 1
                continue

            if args.dry_run:
                log.info("DRY user_id=%s secrets=%d", user_id, len(secrets))
                continue

            sb.table("user_settings").update(
                {
                    "settings": non_secrets,
                    "settings_encrypted": blob,
                    "settings_encrypted_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("user_id", user_id).execute()
            encrypted += 1

        offset += args.batch_size

    log.info("done: total=%d encrypted=%d skipped=%d", total, encrypted, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
