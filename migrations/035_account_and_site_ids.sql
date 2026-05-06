-- Migration 035 — add account_id and site_id to all tenant-scoped tables
--
-- The dashboard now sends X-Sama-Account-Id and X-Sama-Site-Id on every API
-- call. Up to migration 034 every tenant-scoped table only had a single
-- ``tenant_id`` column, which conflated "owning workspace" with "specific
-- site within that workspace" — so a customer with two sites couldn't keep
-- their data partitioned.
--
-- This migration:
--   1. Adds ``account_id`` and ``site_id`` columns (nullable initially so
--      backfill can run).
--   2. Backfills both columns from the legacy ``tenant_id`` value — for
--      existing single-site tenants account_id == site_id == tenant_id.
--   3. Adds composite indexes for the new (account_id, site_id) lookup
--      pattern that route handlers will use going forward.
--
-- The columns are kept NULLABLE in this first wave so the rollout can land
-- before all writers are updated. A follow-up migration will set NOT NULL
-- once telemetry confirms every insert path populates them.

-- ── 1. seo_keywords ────────────────────────────────────────────────────────
ALTER TABLE seo_keywords
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE seo_keywords
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_seo_keywords_account_site
    ON seo_keywords (account_id, site_id);

-- ── 2. seo_audits / seo_strategies / seo_strategy_tasks ────────────────────
ALTER TABLE seo_audits
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE seo_audits
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_seo_audits_account_site
    ON seo_audits (account_id, site_id);

ALTER TABLE seo_strategies
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE seo_strategies
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_seo_strategies_account_site
    ON seo_strategies (account_id, site_id);

-- seo_strategy_tasks may not exist on every install yet; gate the ALTER on
-- to_regclass so the migration is idempotent across environments.
DO $$
BEGIN
    IF to_regclass('public.seo_strategy_tasks') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE seo_strategy_tasks
                 ADD COLUMN IF NOT EXISTS account_id TEXT,
                 ADD COLUMN IF NOT EXISTS site_id    TEXT';
        EXECUTE 'UPDATE seo_strategy_tasks
                    SET account_id = COALESCE(account_id, tenant_id),
                        site_id    = COALESCE(site_id,    tenant_id)
                  WHERE (account_id IS NULL OR site_id IS NULL)
                    AND tenant_id IS NOT NULL';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_seo_strategy_tasks_account_site
                 ON seo_strategy_tasks (account_id, site_id)';
    END IF;
END $$;

-- ── 3. AI visibility tables ────────────────────────────────────────────────
ALTER TABLE ai_visibility_checks
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE ai_visibility_checks
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_av_checks_account_site
    ON ai_visibility_checks (account_id, site_id);

ALTER TABLE ai_visibility_gaps
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE ai_visibility_gaps
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_av_gaps_account_site
    ON ai_visibility_gaps (account_id, site_id);

-- ── 4. agent_runs / agent_actions / agent_reports ──────────────────────────
DO $$
BEGIN
    IF to_regclass('public.agent_runs') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE agent_runs
                 ADD COLUMN IF NOT EXISTS account_id TEXT,
                 ADD COLUMN IF NOT EXISTS site_id    TEXT';
        EXECUTE 'UPDATE agent_runs
                    SET account_id = COALESCE(account_id, tenant_id),
                        site_id    = COALESCE(site_id,    tenant_id)
                  WHERE (account_id IS NULL OR site_id IS NULL)
                    AND tenant_id IS NOT NULL';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_agent_runs_account_site
                 ON agent_runs (account_id, site_id)';
    END IF;
END $$;

ALTER TABLE agent_actions
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE agent_actions
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_agent_actions_account_site
    ON agent_actions (account_id, site_id);

DO $$
BEGIN
    IF to_regclass('public.agent_reports') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE agent_reports
                 ADD COLUMN IF NOT EXISTS account_id TEXT,
                 ADD COLUMN IF NOT EXISTS site_id    TEXT';
        EXECUTE 'UPDATE agent_reports
                    SET account_id = COALESCE(account_id, tenant_id),
                        site_id    = COALESCE(site_id,    tenant_id)
                  WHERE (account_id IS NULL OR site_id IS NULL)
                    AND tenant_id IS NOT NULL';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_agent_reports_account_site
                 ON agent_reports (account_id, site_id)';
    END IF;
END $$;

-- ── 5. Content + SERP ──────────────────────────────────────────────────────
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS account_id TEXT,
    ADD COLUMN IF NOT EXISTS site_id    TEXT;

UPDATE content_pieces
   SET account_id = COALESCE(account_id, tenant_id),
       site_id    = COALESCE(site_id,    tenant_id)
 WHERE account_id IS NULL OR site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_content_pieces_account_site
    ON content_pieces (account_id, site_id);

DO $$
BEGIN
    IF to_regclass('public.serp_results') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE serp_results
                 ADD COLUMN IF NOT EXISTS account_id TEXT,
                 ADD COLUMN IF NOT EXISTS site_id    TEXT';
        EXECUTE 'UPDATE serp_results
                    SET account_id = COALESCE(account_id, tenant_id),
                        site_id    = COALESCE(site_id,    tenant_id)
                  WHERE (account_id IS NULL OR site_id IS NULL)
                    AND tenant_id IS NOT NULL';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_serp_results_account_site
                 ON serp_results (account_id, site_id)';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.web_vitals') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE web_vitals
                 ADD COLUMN IF NOT EXISTS account_id TEXT,
                 ADD COLUMN IF NOT EXISTS site_id    TEXT';
        EXECUTE 'UPDATE web_vitals
                    SET account_id = COALESCE(account_id, tenant_id),
                        site_id    = COALESCE(site_id,    tenant_id)
                  WHERE (account_id IS NULL OR site_id IS NULL)
                    AND tenant_id IS NOT NULL';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_web_vitals_account_site
                 ON web_vitals (account_id, site_id)';
    END IF;
END $$;

-- ── 6. google_connections — sync timestamp surface ─────────────────────────
-- The dashboard's "GSC: Not connected, Last sync: Never" line wants a
-- last_synced_at column to gate keyword visibility on. Add it idempotently
-- so the SEO route can filter rows for tenants that have never synced.
ALTER TABLE google_connections
    ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_google_connections_last_synced
    ON google_connections (tenant_id, service, last_synced_at);
