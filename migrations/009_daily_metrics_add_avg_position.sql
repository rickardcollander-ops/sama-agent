-- Fix: Add avg_position column if it was missing from original daily_metrics creation.
-- Run this in Supabase Dashboard → SQL Editor if you get:
--   "Could not find the 'avg_position' column of 'daily_metrics' in the schema cache"

ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS avg_position FLOAT DEFAULT 0.0;
