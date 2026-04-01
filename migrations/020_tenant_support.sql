-- Migration 020: Multi-tenancy support
-- Adds tenant_id column to key tables so data can be partitioned per tenant.
-- Existing rows default to 'default' (the original Successifier tenant).

-- ============================================================
-- 1. Add tenant_id column with default value
-- ============================================================

ALTER TABLE seo_keywords
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE seo_audits
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE agent_actions
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

-- ============================================================
-- 2. Indexes for tenant-scoped queries
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_seo_keywords_tenant_id ON seo_keywords (tenant_id);
CREATE INDEX IF NOT EXISTS idx_content_pieces_tenant_id ON content_pieces (tenant_id);
CREATE INDEX IF NOT EXISTS idx_seo_audits_tenant_id ON seo_audits (tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_actions_tenant_id ON agent_actions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_tenant_id ON leads (tenant_id);
CREATE INDEX IF NOT EXISTS idx_alerts_tenant_id ON alerts (tenant_id);

-- ============================================================
-- 3. Row Level Security (RLS) policies
--    These policies restrict access so that authenticated users
--    can only see rows matching their tenant_id claim in the JWT.
--    The service-role key bypasses RLS, so backend calls still
--    work without changes.
-- ============================================================

-- Enable RLS on each table (idempotent)
ALTER TABLE seo_keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_pieces ENABLE ROW LEVEL SECURITY;
ALTER TABLE seo_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if re-running migration
DROP POLICY IF EXISTS tenant_isolation_seo_keywords ON seo_keywords;
DROP POLICY IF EXISTS tenant_isolation_content_pieces ON content_pieces;
DROP POLICY IF EXISTS tenant_isolation_seo_audits ON seo_audits;
DROP POLICY IF EXISTS tenant_isolation_agent_actions ON agent_actions;
DROP POLICY IF EXISTS tenant_isolation_leads ON leads;
DROP POLICY IF EXISTS tenant_isolation_alerts ON alerts;

-- Create tenant-isolation policies
-- Uses the custom JWT claim: auth.jwt() ->> 'tenant_id'
-- Falls back to 'default' when no claim is present (backward compat).

CREATE POLICY tenant_isolation_seo_keywords ON seo_keywords
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));

CREATE POLICY tenant_isolation_content_pieces ON content_pieces
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));

CREATE POLICY tenant_isolation_seo_audits ON seo_audits
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));

CREATE POLICY tenant_isolation_agent_actions ON agent_actions
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));

CREATE POLICY tenant_isolation_leads ON leads
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));

CREATE POLICY tenant_isolation_alerts ON alerts
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));
