-- Migration 030 — backing tables for the inline strategy editor (S1),
-- per-piece performance lookup (C6) and content refine flow (C5).
--
-- Adds:
--   1. marketing_strategies.updated_at  — set whenever update_section() runs
--   2. Index on ai_visibility_checks(tenant_id, prompt) so the
--      ilike-substring lookup used by /api/strategy/evaluation and
--      /api/content/pieces/{id}/performance has a usable scan path on
--      large tenants.
--   3. Trigger to keep marketing_strategies.updated_at in sync on every
--      UPDATE — saves the application from having to set it explicitly.

ALTER TABLE marketing_strategies
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

UPDATE marketing_strategies
SET updated_at = COALESCE(updated_at, generated_at, created_at, now())
WHERE updated_at IS NULL;

ALTER TABLE marketing_strategies
  ALTER COLUMN updated_at SET DEFAULT now();

CREATE OR REPLACE FUNCTION marketing_strategies_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS marketing_strategies_touch_updated_at_trg
  ON marketing_strategies;

CREATE TRIGGER marketing_strategies_touch_updated_at_trg
BEFORE UPDATE ON marketing_strategies
FOR EACH ROW
EXECUTE FUNCTION marketing_strategies_touch_updated_at();

-- Indexes for the substring-match lookups used by the new evaluation
-- and per-piece performance endpoints. Postgres can't use a btree
-- index for a leading-wildcard ILIKE; the `pg_trgm` extension gives
-- us a usable GIN index. Skip silently if the extension can't be
-- enabled — the queries still work, just sequentially scan.
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN insufficient_privilege THEN
  -- Some Supabase plans disallow extension creation; that's fine,
  -- the queries fall back to a sequential scan.
  NULL;
END$$;

CREATE INDEX IF NOT EXISTS idx_ai_visibility_checks_prompt_trgm
  ON ai_visibility_checks USING gin (prompt gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_seo_keywords_keyword_trgm
  ON seo_keywords USING gin (keyword gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_content_pieces_target_keyword_trgm
  ON content_pieces USING gin (target_keyword gin_trgm_ops);
