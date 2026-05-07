-- 039_content_suggestions_workflow.sql
-- Per-tenant brand voices + parent/child links to wire the analysis ->
-- content-plan workflow. Strict tenant isolation: tenant_brand_voices
-- has tenant_id as the primary key and a service-role RLS policy so
-- one tenant's voice is never reused for another tenant.

-- ── tenant_brand_voices ────────────────────────────────────────────────────
-- One row per tenant. voice_json mirrors the BrandVoice structure (tone,
-- vocabulary, messaging_pillars, proof_points, sentence_rhythm, target_persona)
-- but is extracted from the customer's own website by brand_voice_scraper.
CREATE TABLE IF NOT EXISTS tenant_brand_voices (
    tenant_id   TEXT PRIMARY KEY,
    voice_json  JSONB NOT NULL,
    source_urls TEXT[] NOT NULL DEFAULT '{}',
    scraped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_brand_voices_scraped
    ON tenant_brand_voices (scraped_at DESC);

ALTER TABLE tenant_brand_voices ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow service role" ON tenant_brand_voices;
CREATE POLICY "Allow service role" ON tenant_brand_voices FOR ALL USING (true);

-- Reuse the set_updated_at_now() function defined in 037_*.sql.
DROP TRIGGER IF EXISTS tenant_brand_voices_updated_at ON tenant_brand_voices;
CREATE TRIGGER tenant_brand_voices_updated_at
    BEFORE UPDATE ON tenant_brand_voices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at_now();

-- ── content_pieces: link social children to their parent article ───────────
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS parent_content_id UUID REFERENCES content_pieces(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS source_analysis_run_id UUID;

CREATE INDEX IF NOT EXISTS idx_content_pieces_parent
    ON content_pieces (parent_content_id)
    WHERE parent_content_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_pieces_source_analysis_run
    ON content_pieces (source_analysis_run_id)
    WHERE source_analysis_run_id IS NOT NULL;

-- ── content_plan_items: link social plan_items to article plan_items ───────
ALTER TABLE content_plan_items
    ADD COLUMN IF NOT EXISTS parent_plan_item_id UUID REFERENCES content_plan_items(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS emailed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_content_plan_items_parent
    ON content_plan_items (parent_plan_item_id)
    WHERE parent_plan_item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_plan_items_emailed_due
    ON content_plan_items (scheduled_for)
    WHERE parent_plan_item_id IS NOT NULL AND emailed_at IS NULL;
