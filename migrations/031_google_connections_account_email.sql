-- Track which Google account each connection authenticated with so the UI
-- can show the user *which* account is currently linked and offer a
-- "switch account" flow when the wrong one is connected.
ALTER TABLE google_connections
  ADD COLUMN IF NOT EXISTS account_email TEXT;
