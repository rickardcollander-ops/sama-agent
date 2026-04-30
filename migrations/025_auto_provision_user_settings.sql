-- Auto-provision a user_settings row whenever a new auth user is created.
-- Lets the dashboard always read from user_settings without race conditions
-- between signup and the first onboarding save.

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.user_settings (user_id, settings, created_at, updated_at)
    VALUES (
        NEW.id,
        jsonb_build_object(
            'plan', 'starter',
            'created_via', 'signup_trigger'
        ),
        NOW(),
        NOW()
    )
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();
