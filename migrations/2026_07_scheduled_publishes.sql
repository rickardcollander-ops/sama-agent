-- 2026-07: dedicated scheduled_publishes table
--
-- Replaces the JSONB blob in ``user_settings.settings.scheduled_publishes``.
-- Reasons: indexable on scheduled_at (the scheduler can ORDER BY + LIMIT,
-- avoiding a full scan every minute); concurrency-safe writes (no JSONB
-- read-modify-write race); RLS protects per-tenant access; logical replica
-- traffic is small.
--
-- Migration plan: dual-write from the publishing API for two weeks, then
-- backfill, then flip ``READ_FROM_SCHEDULED_PUBLISHES_TABLE=1`` and stop
-- writing to the JSONB column.

CREATE TABLE IF NOT EXISTS scheduled_publishes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    site_id         UUID,
    content_type    TEXT NOT NULL,         -- 'blog_post' | 'social_post' | 'ad_creative'
    content_id      UUID,                  -- FK into the relevant content table (lossy: no constraint)
    scheduled_at    TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'published' | 'failed' | 'cancelled'
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scheduled_publishes_due_idx
    ON scheduled_publishes (scheduled_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS scheduled_publishes_tenant_idx
    ON scheduled_publishes (tenant_id);
CREATE INDEX IF NOT EXISTS scheduled_publishes_status_idx
    ON scheduled_publishes (status);

ALTER TABLE scheduled_publishes ENABLE ROW LEVEL SECURITY;
CREATE POLICY scheduled_publishes_tenant_isolation ON scheduled_publishes
    FOR ALL TO authenticated
    USING (tenant_id = (auth.jwt() ->> 'sub'))
    WITH CHECK (tenant_id = (auth.jwt() ->> 'sub'));

CREATE OR REPLACE FUNCTION scheduled_publishes_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scheduled_publishes_touch ON scheduled_publishes;
CREATE TRIGGER scheduled_publishes_touch
    BEFORE UPDATE ON scheduled_publishes
    FOR EACH ROW EXECUTE FUNCTION scheduled_publishes_touch_updated_at();
