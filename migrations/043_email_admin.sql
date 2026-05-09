-- Admin-driven email schedule + log indexes for the /c/admin/email page.
-- The dashboard reads/writes email_schedules to control when the agent's
-- scheduled email jobs run; the agent's scheduler reloads from this table
-- once a minute (see shared/scheduler.py).

CREATE TABLE IF NOT EXISTS email_schedules (
    kind              TEXT PRIMARY KEY,
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    cron_day_of_week  TEXT,                    -- 'mon'..'sun', or NULL for daily/hourly
    cron_hour         INT,                     -- 0..23, or NULL for hourly
    cron_minute       INT NOT NULL DEFAULT 0,  -- 0..59
    timezone          TEXT NOT NULL DEFAULT 'UTC',
    description       TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by        TEXT
);

INSERT INTO email_schedules (kind, enabled, cron_day_of_week, cron_hour, cron_minute, description)
VALUES
    ('weekly_status', TRUE, 'mon', 9,    0,  'Veckostatus per användare (måndag 09:00 UTC)'),
    ('social_posts',  TRUE, NULL,  NULL, 15, 'Sociala posts-mail (timvis vid :15)')
ON CONFLICT (kind) DO NOTHING;

-- Backfill the index the admin log view filters by. The existing
-- (user_id, kind, created_at) index doesn't cover the all-users
-- timeline query the admin page runs.
CREATE INDEX IF NOT EXISTS idx_email_send_log_created
    ON email_send_log (created_at DESC);
