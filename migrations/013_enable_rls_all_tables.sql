-- Enable Row Level Security on all public tables missing RLS
-- Fixes Supabase Security Advisor errors for 9 tables
-- Run this in Supabase Dashboard → SQL Editor

-- 1. daily_metrics (was explicitly disabled in 008)
ALTER TABLE daily_metrics ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "Allow all for service role" ON daily_metrics FOR ALL USING (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 2. agent_actions
ALTER TABLE agent_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON agent_actions FOR ALL USING (true);

-- 3. chat_history
ALTER TABLE chat_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON chat_history FOR ALL USING (true);

-- 4. agent_cycles
ALTER TABLE agent_cycles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON agent_cycles FOR ALL USING (true);

-- 5. agent_learnings
ALTER TABLE agent_learnings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON agent_learnings FOR ALL USING (true);

-- 6. agent_goals
ALTER TABLE agent_goals ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON agent_goals FOR ALL USING (true);

-- 7. ai_visibility_checks
ALTER TABLE ai_visibility_checks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON ai_visibility_checks FOR ALL USING (true);

-- 8. ai_visibility_gaps
ALTER TABLE ai_visibility_gaps ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON ai_visibility_gaps FOR ALL USING (true);
