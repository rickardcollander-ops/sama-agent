-- Per-audit progress tracking. The dashboard's running-jobs widget polls
-- the audit row and shows "Analyserar X av Y sidor" with a real progress
-- bar instead of a time-based estimate. Both columns are nullable for
-- legacy rows; the audit agent updates them as it crawls.
ALTER TABLE site_audits
  ADD COLUMN IF NOT EXISTS pages_total INT,
  ADD COLUMN IF NOT EXISTS pages_done INT;
