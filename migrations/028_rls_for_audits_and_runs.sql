-- Enable RLS + permissive service-role policies for the tables added in
-- 026/027. Without these policies, INSERTs via the API silently fail and
-- /api/site-audit/run + /api/analysis/run return {"id": null}, breaking the
-- dashboard's polling flow ("Backend did not return an audit id").
--
-- Mirrors the pattern in 013_enable_rls_all_tables.sql.

ALTER TABLE site_audits ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "Allow all for service role" ON site_audits FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE analysis_runs ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "Allow all for service role" ON analysis_runs FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE pending_approvals ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "Allow all for service role" ON pending_approvals FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
