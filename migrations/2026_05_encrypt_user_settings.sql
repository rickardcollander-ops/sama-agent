-- 2026-05: encrypt secrets in user_settings
--
-- Adds a parallel TEXT column ``settings_encrypted`` that holds the
-- envelope-encrypted JSON blob for fields like *_api_key, *_token, *_secret.
-- The plaintext ``settings`` JSONB column stays in place during the
-- backfill window.
--
-- Roll-out:
--   1. Apply this migration (additive, zero-downtime).
--   2. Deploy the application code; new writes populate both columns.
--   3. Run ``scripts/backfill_user_settings_encryption.py`` to migrate
--      existing rows.
--   4. Flip ``READ_ENCRYPTED_ONLY=1``. Verify dashboards.
--   5. After 30 days of stability, drop secret fields from the plaintext
--      column with a follow-up migration:
--         UPDATE user_settings SET settings = settings - ARRAY[...secret keys...]
--      and finally ``ALTER TABLE user_settings DROP COLUMN settings`` if the
--      whole column is to be replaced.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS settings_encrypted TEXT,
    ADD COLUMN IF NOT EXISTS settings_encrypted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS user_settings_encrypted_at_idx
    ON user_settings (settings_encrypted_at);

COMMENT ON COLUMN user_settings.settings_encrypted IS
    'Envelope-encrypted secret fields. Format: v1$<nonceM>$<wrappedDEK>$<nonceD>$<ct>. See shared/secrets_vault.py.';
