-- 2026-06: complete tenant Row-Level Security on agent tables
--
-- Tables that previously lived behind ``USING (true)`` policies (effectively
-- no RLS) and now require ``tenant_id = auth.jwt() ->> 'sub'`` for end-user
-- access. The service-role key bypasses RLS, so the FastAPI agent backend
-- (which uses the service-role) keeps full read/write access.
--
-- Roll-out order:
--   1. Apply this migration to a STAGING Supabase project. Validate that the
--      dashboard's customer portal still loads — every tenant query hits
--      auth.jwt() and the policies match.
--   2. Apply to production within a maintenance window. Service-role traffic
--      is unaffected; user-JWT traffic gets immediate RLS protection.
--   3. Remove the legacy ``allow_all_*`` policies (commented below) once the
--      new policies are confirmed working.

-- ── agent_actions ────────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS agent_actions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS allow_all_agent_actions ON agent_actions;
CREATE POLICY agent_actions_tenant_isolation ON agent_actions
    FOR ALL
    TO authenticated
    USING (tenant_id = (auth.jwt() ->> 'sub'))
    WITH CHECK (tenant_id = (auth.jwt() ->> 'sub'));

-- ── chat_history ─────────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS chat_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS allow_all_chat_history ON chat_history;
CREATE POLICY chat_history_tenant_isolation ON chat_history
    FOR ALL
    TO authenticated
    USING (tenant_id = (auth.jwt() ->> 'sub'))
    WITH CHECK (tenant_id = (auth.jwt() ->> 'sub'));

-- ── agent_learnings ──────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS agent_learnings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS allow_all_agent_learnings ON agent_learnings;
CREATE POLICY agent_learnings_tenant_isolation ON agent_learnings
    FOR ALL
    TO authenticated
    USING (tenant_id = (auth.jwt() ->> 'sub'))
    WITH CHECK (tenant_id = (auth.jwt() ->> 'sub'));

-- ── tenant_usage ─────────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS tenant_usage ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS allow_all_tenant_usage ON tenant_usage;
CREATE POLICY tenant_usage_tenant_isolation ON tenant_usage
    FOR ALL
    TO authenticated
    USING (tenant_id = (auth.jwt() ->> 'sub'))
    WITH CHECK (tenant_id = (auth.jwt() ->> 'sub'));

-- ── agent_runs ───────────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS agent_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS allow_all_agent_runs ON agent_runs;
CREATE POLICY agent_runs_tenant_isolation ON agent_runs
    FOR ALL
    TO authenticated
    USING (tenant_id = (auth.jwt() ->> 'sub'))
    WITH CHECK (tenant_id = (auth.jwt() ->> 'sub'));

-- ── tenant_access_log (P0-2 audit table) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_access_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id       TEXT,
    requested_id    TEXT,
    path            TEXT,
    reason          TEXT,
    ip              INET,
    user_agent      TEXT
);
CREATE INDEX IF NOT EXISTS tenant_access_log_occurred_at_idx
    ON tenant_access_log (occurred_at);
CREATE INDEX IF NOT EXISTS tenant_access_log_tenant_id_idx
    ON tenant_access_log (tenant_id);

ALTER TABLE tenant_access_log ENABLE ROW LEVEL SECURITY;
-- Only service-role reads/writes this table; no user-facing policies.
