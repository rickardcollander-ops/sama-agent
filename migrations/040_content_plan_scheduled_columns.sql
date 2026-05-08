-- 040_content_plan_scheduled_columns.sql
-- Ensure content_plan_items has the scheduling columns the agent and the
-- /api/content/plan/calendar endpoint rely on. Older deployments grew these
-- ad hoc; this migration is idempotent and safe to re-run.

ALTER TABLE content_plan_items
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS auto_publish_on_schedule BOOLEAN NOT NULL DEFAULT FALSE;

-- The scheduler scans by (status, scheduled_for) hourly; index it to keep
-- the lookup cheap as the table grows.
CREATE INDEX IF NOT EXISTS idx_content_plan_items_due
    ON content_plan_items (status, scheduled_for)
    WHERE scheduled_for IS NOT NULL;
