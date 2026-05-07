-- 038_content_plan_source_column.sql
-- Tag each plan item with where it came from so the dashboard can render
-- one unified "what to write next" list with source-based filter chips.
--
-- Allowed values:
--   manual          — user added by hand
--   ai_generated    — produced by /api/content/plan/generate
--   analysis_gap    — auto-fed from a content analysis run (keyword gap)
--   competitor_gap  — auto-fed from competitor coverage analysis

ALTER TABLE content_plan_items
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual';

CREATE INDEX IF NOT EXISTS idx_content_plan_items_source
  ON content_plan_items(tenant_id, source);

-- Dedup support: when the analysis cycle re-discovers the same keyword,
-- we want to upsert by (tenant_id, lower(target_keyword)) without creating
-- a duplicate plan row. A partial unique index on lowercased keyword does
-- the job — only enforced for rows that actually have a keyword.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_content_plan_keyword_per_tenant
  ON content_plan_items(tenant_id, lower(target_keyword))
  WHERE target_keyword IS NOT NULL AND target_keyword <> '';
