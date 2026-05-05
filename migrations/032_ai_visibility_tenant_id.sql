-- Migration 032 — add tenant_id to AI visibility tables
--
-- Migration 020 added tenant_id to seo_keywords, content_pieces, etc.
-- but missed ai_visibility_checks / ai_visibility_gaps. The agent has been
-- writing tenant_id into the insert payload all along, so every per-tenant
-- run silently failed with PGRST204 ("Could not find the 'tenant_id'
-- column"), and the GEO panel showed "Ingen data än" even though the
-- agent_runs row reported "5 checks completed".
--
-- This migration backfills the column so per-tenant inserts succeed and
-- the tenant-scoped GET /summary and /checks queries return rows.
-- Existing rows (from the legacy single-tenant deployment) default to
-- 'default', matching the convention used elsewhere in migration 020.

ALTER TABLE ai_visibility_checks
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE ai_visibility_gaps
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_av_checks_tenant_id
    ON ai_visibility_checks (tenant_id);

CREATE INDEX IF NOT EXISTS idx_av_gaps_tenant_id
    ON ai_visibility_gaps (tenant_id);

-- The (tenant_id, prompt) composite was promised in migration 030's header
-- but never created because the column didn't exist. Add it now so the
-- substring lookups in /api/strategy/evaluation and the per-piece
-- performance endpoints have the access path the comment described.
CREATE INDEX IF NOT EXISTS idx_ai_visibility_checks_tenant_prompt
    ON ai_visibility_checks (tenant_id, prompt);
