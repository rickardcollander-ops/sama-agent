-- 2026-08: append-only audit log for sensitive mutations
--
-- Captures who did what to which resource, with enough context that
-- forensic / compliance / customer-support questions can be answered
-- without grovelling through application logs.
--
-- INSERT-only. Update/Delete are denied at the policy level — even the
-- service-role key cannot mutate rows once written. Rotate / archive via
-- partition drop or scheduled BigQuery export.

CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id       TEXT,
    actor_user_id   UUID,
    actor_email     TEXT,
    actor_role      TEXT,                 -- 'user' | 'admin' | 'service' | 'agent'
    action          TEXT NOT NULL,        -- e.g. 'user_settings.update'
    resource_type   TEXT,                 -- 'user_settings' | 'agent_run' | …
    resource_id     TEXT,
    diff            JSONB,                -- {before: {...}, after: {...}}
    ip              INET,
    user_agent      TEXT,
    request_id      TEXT
);

CREATE INDEX IF NOT EXISTS audit_log_occurred_idx ON audit_log (occurred_at);
CREATE INDEX IF NOT EXISTS audit_log_tenant_idx ON audit_log (tenant_id);
CREATE INDEX IF NOT EXISTS audit_log_action_idx ON audit_log (action);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
-- No SELECT/INSERT/UPDATE/DELETE policies for the ``authenticated`` role —
-- only service-role inserts; reads happen out-of-band by ops via the
-- service-role key.

CREATE OR REPLACE FUNCTION audit_log_block_mutations() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutations();
