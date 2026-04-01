# SAMA Commercialization Plan

## Overview

SAMA (Successifier Autonomous Marketing Agent) is being transformed from a single-tenant system hardcoded for Successifier into a multi-tenant SaaS product. This document summarizes the architecture changes and next steps.

## Architecture Changes

### Multi-Tenancy Layer

| Component | File | Purpose |
|---|---|---|
| TenantConfig | `shared/tenant.py` | Per-tenant settings loaded from `user_settings` table, with env-variable fallback and 5-min cache |
| TenantMiddleware | `shared/tenant_middleware.py` | FastAPI middleware that extracts `tenant_id` from `X-Tenant-ID` header, `tenant_id` query param, or defaults to `"default"` |
| Agent Factory | `shared/tenant_agents.py` | Async factory functions that instantiate agents with tenant-specific config |
| SQL Migration | `migrations/020_tenant_support.sql` | Adds `tenant_id` column, indexes, and RLS policies to core tables |

### Agent Updates

All agents (`seo`, `content`, `social`, `reviews`, `analytics`) now accept an optional `tenant_config` parameter in their `__init__`. When provided, they use the tenant's domain, API keys, competitors, and other settings instead of the global `settings` singleton.

**Backward compatibility is preserved.** When `tenant_config` is `None` (or not provided), agents fall back to the global `settings` object, so the existing Successifier deployment continues working without any configuration changes.

### Tenant Config Properties

The `TenantConfig` class provides properties for:

- **Brand/Domain**: `brand_name`, `domain`, `site_url`, `cms_api_url`
- **SEO**: `gsc_site_url`, `competitors`
- **Analytics**: `ga4_property_id`
- **API Keys**: Anthropic, SEMrush, Google, Twitter, LinkedIn, Reddit
- **Brand Voice**: `brand_voice_tone`, `messaging_pillars`, `proof_points`
- **Automation**: `auto_publish_blog_posts`, `auto_publish_social_posts`, review auto-response flags
- **Reviews**: `review_platforms` (G2, Capterra, Trustpilot URLs and config)

All properties fall back to environment variables when no tenant-specific value exists in the database.

### Automation Routes

`api/routes/automation.py` now accepts an optional `tenant_id` in the request body or via the middleware. When a non-default tenant is specified, the route uses the agent factory to create tenant-scoped agent instances.

## What's Next

### Phase 2: Billing and Auth
- Stripe integration for subscription management
- Tenant provisioning on signup (auto-create `user_settings` row)
- JWT-based auth with `tenant_id` claim for RLS enforcement
- Usage metering (API calls, content generated, etc.)

### Phase 3: Onboarding
- Self-service onboarding wizard (domain, API keys, brand voice)
- Pre-built templates for common industries
- Import/export for tenant configuration

### Phase 4: Isolation and Scaling
- Per-tenant rate limiting
- Tenant-scoped background job scheduling
- Data export/deletion for tenant offboarding (GDPR)
- Optional dedicated infrastructure for enterprise tenants

## Migration Guide (Existing Successifier Setup)

The existing Successifier deployment requires **zero changes** to keep working:

1. All environment variables continue to work as before.
2. The `"default"` tenant ID is used when no `X-Tenant-ID` header or `tenant_id` param is provided.
3. Global agent singletons (`seo_agent`, `content_agent`, etc.) are still instantiated at module level with no `tenant_config`, so they use env vars.
4. The SQL migration sets `tenant_id = 'default'` on all existing rows.

To onboard a **new tenant**:

1. Create a row in `user_settings` with the tenant's `user_id` and a `settings` JSONB object containing their domain, API keys, competitors, etc.
2. Pass `X-Tenant-ID: <user_id>` in API requests (or `?tenant_id=<user_id>`).
3. Run `migrations/020_tenant_support.sql` to add the `tenant_id` column to core tables.
