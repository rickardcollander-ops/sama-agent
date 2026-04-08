-- Migration 024: Add tenant_id to seo_strategies
-- Follows the same pattern as migration 020 for consistency.

ALTER TABLE seo_strategies
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_seo_strategies_tenant_id ON seo_strategies (tenant_id);

ALTER TABLE seo_strategies ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_seo_strategies ON seo_strategies;

CREATE POLICY tenant_isolation_seo_strategies ON seo_strategies
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));
