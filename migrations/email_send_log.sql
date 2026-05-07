-- Log of every transactional email the agent sends.
-- Used to (a) avoid duplicate weekly sends, (b) debug delivery failures,
-- (c) show users a "last sent" timestamp in their notification settings.

CREATE TABLE IF NOT EXISTS email_send_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID,
    recipient    TEXT NOT NULL,
    kind         TEXT NOT NULL,                 -- 'weekly_status' | 'alert' | ...
    subject      TEXT NOT NULL,
    status       TEXT NOT NULL,                 -- 'sent' | 'error'
    message_id   TEXT,                          -- Resend message id when sent
    error        TEXT,
    stats        JSONB DEFAULT '{}'::jsonb,     -- snapshot of counts at send time
    test         BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_send_log_user_kind_created
    ON email_send_log (user_id, kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_email_send_log_kind_created
    ON email_send_log (kind, created_at DESC);
