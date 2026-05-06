-- Migration 034: add tenant_id to seo_strategies and seo_audits
-- Run this in the Supabase SQL editor.

ALTER TABLE seo_strategies ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE seo_audits    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_seo_strategies_tenant_id ON seo_strategies (tenant_id);
CREATE INDEX IF NOT EXISTS idx_seo_audits_tenant_id     ON seo_audits    (tenant_id);
