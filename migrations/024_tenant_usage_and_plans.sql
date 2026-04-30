-- Per-tenant monthly usage counters for plan-limit enforcement.
-- See shared/usage.py.

CREATE TABLE IF NOT EXISTS tenant_usage (
    tenant_id   TEXT NOT NULL,
    month       DATE NOT NULL,           -- always the 1st of the month
    metric      TEXT NOT NULL,           -- content_pieces, ad_creatives, agent_runs, review_responses
    count       INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, month, metric)
);

CREATE INDEX IF NOT EXISTS idx_tenant_usage_tenant_month
    ON tenant_usage (tenant_id, month);

ALTER TABLE tenant_usage ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS; this policy allows authenticated users to read
-- their own usage rows (so the dashboard can display "X / Y used").
DROP POLICY IF EXISTS tenant_usage_self_read ON tenant_usage;
CREATE POLICY tenant_usage_self_read ON tenant_usage
    FOR SELECT
    USING (tenant_id = (auth.jwt() ->> 'sub'));

-- Index to make the scheduler's "due posts" query fast.
CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled
    ON social_posts (status, scheduled_for)
    WHERE status = 'scheduled';

-- Validation columns on content_pieces (used by /api/content/pieces/{id}/validate).
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS validation_score INTEGER,
    ADD COLUMN IF NOT EXISTS validation_notes JSONB,
    ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS external_url TEXT;

-- Draft column for AI-generated review responses awaiting operator approval.
ALTER TABLE reviews
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default',
    ADD COLUMN IF NOT EXISTS draft_response TEXT,
    ADD COLUMN IF NOT EXISTS draft_generated_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_reviews_tenant_id ON reviews (tenant_id);
