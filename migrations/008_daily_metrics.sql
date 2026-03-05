-- Daily Metrics table
-- Stores aggregated daily stats per channel for the analytics dashboard.
-- Run this in Supabase Dashboard → SQL Editor

CREATE TABLE IF NOT EXISTS daily_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    channel VARCHAR(50) NOT NULL,
    total_sessions INTEGER DEFAULT 0,
    total_conversions FLOAT DEFAULT 0,
    total_revenue FLOAT DEFAULT 0.0,
    total_ad_spend FLOAT DEFAULT 0.0,
    avg_position FLOAT DEFAULT 0.0,
    total_clicks INTEGER DEFAULT 0,
    total_impressions INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(date, channel)
);

CREATE INDEX IF NOT EXISTS idx_daily_metrics_date ON daily_metrics(date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_metrics_channel ON daily_metrics(channel, date DESC);

-- Disable RLS so the service-role key can insert/read without policies
ALTER TABLE daily_metrics DISABLE ROW LEVEL SECURITY;
