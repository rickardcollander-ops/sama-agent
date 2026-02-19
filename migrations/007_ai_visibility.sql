-- AI Visibility / GEO Monitoring tables
-- Run in Supabase Dashboard → SQL Editor

-- Checks table: one row per prompt × AI engine
CREATE TABLE IF NOT EXISTS ai_visibility_checks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      TEXT,
    prompt      TEXT NOT NULL,
    category    TEXT NOT NULL,
    ai_engine   TEXT,
    mentioned   BOOLEAN NOT NULL DEFAULT FALSE,
    rank        INTEGER,
    competitors_mentioned TEXT[] DEFAULT '{}',
    sentiment   TEXT,
    ai_response_excerpt TEXT,
    full_response TEXT,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Gaps table: one row per prompt × engine where Successifier was NOT mentioned
CREATE TABLE IF NOT EXISTS ai_visibility_gaps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt      TEXT NOT NULL,
    category    TEXT NOT NULL,
    ai_engine   TEXT,
    priority    TEXT NOT NULL DEFAULT 'medium',
    action_type TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add missing columns if tables already exist (safe to run multiple times)
ALTER TABLE ai_visibility_checks ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE ai_visibility_checks ADD COLUMN IF NOT EXISTS ai_engine TEXT;
ALTER TABLE ai_visibility_checks ADD COLUMN IF NOT EXISTS full_response TEXT;
ALTER TABLE ai_visibility_gaps   ADD COLUMN IF NOT EXISTS ai_engine TEXT;

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_av_checks_checked_at  ON ai_visibility_checks(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_av_checks_run_id      ON ai_visibility_checks(run_id);
CREATE INDEX IF NOT EXISTS idx_av_gaps_status        ON ai_visibility_gaps(status);
