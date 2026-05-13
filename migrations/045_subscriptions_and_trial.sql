-- Stripe subscriptions + 3-day trial for new signups.
--
-- All subscription state lives on user_settings.settings (JSONB) so we keep
-- one row per user and don't have to introduce a new table or RLS policy.
-- See shared/subscription.py for the read path and api/routes/subscriptions.py
-- for the write path. shared/usage.py uses these fields to gate billable
-- operations once the trial expires and the user has no active subscription.
--
-- Fields stored under user_settings.settings:
--   plan                  -- "starter" | "growth" | "enterprise" (existing)
--   plan_status           -- "trial" | "active" | "past_due" | "canceled" | "expired" | "admin_granted"
--   trial_started_at      -- ISO timestamp; set on signup
--   trial_ends_at         -- ISO timestamp; trial_started_at + 3 days
--   stripe_customer_id    -- Stripe customer id once checkout begins
--   stripe_subscription_id -- Stripe subscription id once active
--   admin_granted_until   -- ISO timestamp or null (null = unlimited free access)
--   admin_granted_by      -- admin email that granted access (audit trail)
--   admin_granted_at      -- ISO timestamp the grant was made
--   subscription_current_period_end -- mirror of stripe sub current_period_end

-- 1) Update the signup trigger so new auth users start with a 3-day trial.
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    trial_started TIMESTAMPTZ := NOW();
    trial_ends    TIMESTAMPTZ := NOW() + INTERVAL '3 days';
BEGIN
    INSERT INTO public.user_settings (user_id, settings, created_at, updated_at)
    VALUES (
        NEW.id,
        jsonb_build_object(
            'plan', 'growth',
            'plan_status', 'trial',
            'trial_started_at', to_char(trial_started AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
            'trial_ends_at',    to_char(trial_ends    AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
            'created_via', 'signup_trigger'
        ),
        NOW(),
        NOW()
    )
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
$$;

-- Trigger already exists from migration 025; recreating it is idempotent.
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();

-- 2) Backfill existing user_settings rows that have no plan_status. They
--    were created before the trial flow existed, so we treat them as
--    "trial" with trial_ends_at = created_at + 3 days. If their created_at
--    is already older than 3 days they immediately read as expired — which
--    is the right call: they signed up, didn't pay, and we now require
--    either a Stripe subscription or an admin grant.
UPDATE public.user_settings us
SET settings = us.settings
    || jsonb_build_object(
        'plan_status', 'trial',
        'trial_started_at', to_char(us.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'trial_ends_at',    to_char((us.created_at + INTERVAL '3 days') AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ),
    updated_at = NOW()
WHERE (us.settings ->> 'plan_status') IS NULL;

-- 3) Audit log for admin grants. Separate table so we can revoke without
--    losing history and so a non-admin can never read it (RLS denies all).
CREATE TABLE IF NOT EXISTS public.subscription_admin_grants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    action        TEXT NOT NULL,            -- 'grant' | 'revoke'
    granted_until TIMESTAMPTZ,              -- null = unlimited
    admin_email   TEXT NOT NULL,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_subscription_admin_grants_user
    ON public.subscription_admin_grants (user_id, created_at DESC);

ALTER TABLE public.subscription_admin_grants ENABLE ROW LEVEL SECURITY;
-- No SELECT/INSERT policies: only the service role (backend) touches this table.
