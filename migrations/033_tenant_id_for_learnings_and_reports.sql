-- Migration 033: Add tenant_id to agent_learnings and agent_reports
-- These tables were created before multi-tenancy and were missing the column.
-- The service role key bypasses RLS, so we must filter manually in Python.

ALTER TABLE agent_learnings
  ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE agent_reports
  ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_agent_learnings_tenant_id
  ON agent_learnings (tenant_id);

CREATE INDEX IF NOT EXISTS idx_agent_reports_tenant_id
  ON agent_reports (tenant_id);

-- Composite indexes for the common (agent_name, tenant_id) filter pattern
CREATE INDEX IF NOT EXISTS idx_agent_learnings_agent_tenant
  ON agent_learnings (agent_name, tenant_id);

CREATE INDEX IF NOT EXISTS idx_agent_reports_agent_tenant
  ON agent_reports (agent_name, tenant_id);
