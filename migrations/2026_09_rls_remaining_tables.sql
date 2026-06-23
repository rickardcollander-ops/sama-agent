-- 2026-09: enable Row-Level Security on the remaining public tables.
--
-- Supabase Security Advisor flagged two critical issues:
--   • rls_disabled_in_public   — table reachable by anon/authenticated with no RLS
--   • sensitive_columns_exposed — a table holding tokens/credentials is API-reachable
--
-- Root cause: these tables were created in earlier migrations without ever
-- running ``ENABLE ROW LEVEL SECURITY``. Every other agent table was locked
-- down in 013 / 028 / 039 / 2026_06_rls_complete; these slipped through.
--
-- Access model: the dashboard reads ALL of these tables exclusively through the
-- FastAPI backend, which uses the Supabase *service-role* key. The service-role
-- BYPASSES RLS, so enabling RLS with no public policy locks out anon +
-- authenticated (PostgREST / browser anon key) while leaving the backend fully
-- functional. None of these tables are read directly from the browser anon
-- client (verified: the only browser-adjacent one, email_send_log, is queried
-- only from server-side API routes that use the service-role key).
--
-- Roll-out: safe to apply to production directly. Service-role traffic is
-- unaffected; anon/authenticated traffic that was previously wide open is now
-- denied. Realtime subscriptions on notifications/reviews/social_posts degrade
-- gracefully to the existing polling fallback (which goes through the backend).

-- ── CRITICAL: token / credential tables (sensitive_columns_exposed) ──────────
-- These hold OAuth access/refresh tokens. They must never be reachable by the
-- client under any role. Service-role only — no policy = deny all to others.
ALTER TABLE IF EXISTS google_connections        ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for service role" ON google_connections;

ALTER TABLE IF EXISTS ad_platform_credentials   ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for service role" ON ad_platform_credentials;

-- ── Ads / content tables (rls_disabled_in_public) ───────────────────────────
ALTER TABLE IF EXISTS ad_creatives              ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS content_plan_items        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS content_analysis_cache    ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS content_analysis_history  ENABLE ROW LEVEL SECURITY;

-- ── Agent control ───────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS tenant_agent_config       ENABLE ROW LEVEL SECURITY;

-- ── GTM agent tables ────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS gtm_icp_analyses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS gtm_strategies            ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS gtm_signals               ENABLE ROW LEVEL SECURITY;

-- ── Reviews / social / notifications (realtime-backed; polling fallback) ─────
ALTER TABLE IF EXISTS reviews                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS review_responses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS social_posts              ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS notifications             ENABLE ROW LEVEL SECURITY;

-- ── SEO strategies ──────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS seo_strategies            ENABLE ROW LEVEL SECURITY;

-- ── Email send log (server-only, service-role) ──────────────────────────────
ALTER TABLE IF EXISTS email_send_log            ENABLE ROW LEVEL SECURITY;

-- ── Verify: every public table should now report rowsecurity = true ─────────
-- SELECT relname, relrowsecurity
--   FROM pg_class
--  WHERE relkind = 'r'
--    AND relnamespace = 'public'::regnamespace
--    AND NOT relrowsecurity;
-- (expect zero rows)
