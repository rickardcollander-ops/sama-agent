-- AI-readability score columns on site_audits.
-- Detail data lives inside payload->'ai_readability'; we top up the overall
-- score and run timestamp into dedicated columns so the dashboard can fetch
-- "latest score per tenant" without dragging down the whole JSONB blob.
ALTER TABLE site_audits
  ADD COLUMN IF NOT EXISTS ai_readability_score INT,
  ADD COLUMN IF NOT EXISTS ai_readability_run_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_site_audits_ai_readability
  ON site_audits(tenant_id, ai_readability_run_at DESC NULLS LAST);
