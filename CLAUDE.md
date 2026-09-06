## Content Agent — Cron Contract

The frontend (`sama-dashboard`) triggers the content agent from two scheduled
cron jobs. The endpoint must support all parameters below.

### Endpoint

```
POST /api/tenant/agents/content/trigger
Header: X-Tenant-ID: <user_id>
Header: X-Sama-Intent: user-action
```

### Parameters (all optional, use defaults if absent)

| Field                    | Type    | Default | Description |
|--------------------------|---------|---------|-------------|
| source                   | string  | —       | `"daily_cron"` or `"weekly_cron"` or `"manual"` |
| ideas_per_run            | number  | 6       | How many ideas to generate |
| auto_draft_top_n         | number  | 3       | Immediately draft the top N ideas (LLM article generation) |
| auto_publish             | boolean | false   | Publish drafts that meet `min_score_for_publish` |
| min_score_for_publish    | number  | 70      | Score threshold (0–100) for auto-publish |
| scheduled_for_days_ahead | number  | —       | Schedule the drafted article N days from today (e.g. 2 = day after tomorrow). If absent, backend decides the date. |

### Two cron callers

**Daily cron** — runs every day 06:00 Europe/Stockholm for onboarded sites
with `user_sites.settings.content_autopilot.enabled = true` (opt-in, same
gate as the weekly cron; sites onboarded < 30 days ago are skipped):

```json
{
  "source": "daily_cron",
  "ideas_per_run": 1,
  "auto_draft_top_n": 1,
  "auto_publish": <site content_autopilot.auto_publish>,
  "min_score_for_publish": <site setting, default 70>,
  "scheduled_for_days_ahead": 2
}
```

Intent: generate 1 idea and write the article for the day after tomorrow. This is the core
rolling flow that keeps the content calendar continuously filled.

**Weekly autopilot** — runs every Monday 07:30 Europe/Stockholm, only for sites
with `user_sites.settings.content_autopilot.enabled = true`. The payload
forwards the site's own autopilot settings (defaults shown):

```json
{
  "source": "weekly_cron",
  "ideas_per_run": 6,
  "auto_draft_top_n": 3,
  "auto_publish": <site content_autopilot.auto_publish, default false>,
  "min_score_for_publish": 70
}
```

Intent: generate a batch of ideas and draft the best ones for manual review.

### Expected behaviour

1. Generate `ideas_per_run` ideas → insert into `content_plan_items` with `status = "idea"`
2. If `auto_draft_top_n > 0`: draft the top N scored ideas (async LLM call, 30–90 s)
   - If `scheduled_for_days_ahead` is set: pin the `scheduled_for` date to today + N days
   - Create `content_pieces` rows with `status = "draft"`
3. **Mode = fully automatic (`auto_publish = true`) and score ≥ `min_score_for_publish`:**
   flip the `content_pieces` row to `status = "approved"` and set
   `auto_publish_on_schedule = true` on the `content_plan_items` row (the
   calendar row the dashboard bridge reads). **Do not publish here.**
4. **Otherwise (review-first mode, or score below threshold):** insert into `approvals`
   with `status = "pending"`. Approving in `/c/approvals` flips the piece to
   `status = "approved"` and sets `scheduled_for = now`.
5. Return immediately with `{ run_id, status: "running" }` — work happens async

> **Publishing is owned by the dashboard, not the backend.** The backend's job ends at
> generate → draft → schedule/approve. The dashboard's 5-min publish cron
> (`/api/integrations/cron` → auto-publish bridge) ingests pieces whose `piece_status`
> is `"approved"` with a due `scheduled_for` and ships them to that tenant's own
> destination (CMS or GitHub). The backend's old hardcoded-GitHub publish
> (`process_due_scheduled_items`) has been removed entirely to avoid
> double-publishing.

> **Critical:** `scheduled_for_days_ahead: 2` pins the article to the correct calendar
> date. A fully-automatic piece auto-publishes on that date; a review-first piece
> publishes within ~5 min of approval.

### Per-site language and brand voice

Every tenant is one **site**, and each site has its own language and its own
brand. The generation paths honour both:

* **Language** comes from `TenantConfig.language` — `user_sites.settings.content_language`
  first, then the domain's country-code TLD (`.se` → `sv`, `.no` → `nb`, `.dk` → `da`),
  then `"en"`. So `supportifier.se` writes Swedish before anyone opens the
  language selector. `shared/language.py` holds the prompt directive
  (`language_instruction`), the readable name for prompts (`language_name`),
  the localized headings the renderer emits itself (`structural_labels` — table
  of contents, key takeaways, FAQ), and a `slugify` that transliterates Nordic
  letters instead of deleting them (`kundnöjdhet` → `kundnojdhet`, not
  `kundnjdhet`).
* The drafted `content_pieces` row records the language it was written in
  (`migrations/2026_09_content_piece_language.sql`). Inserts go through
  `shared.database.insert_content_piece`, which retries without the column if
  the migration has not been applied yet.
* **Brand voice** falls back to `BrandVoice.neutral(tenant_id)` when a site has
  no `tenant_brand_voices` row. Never `BrandVoice.for_tenant("default")` — that
  is Successifier's own profile (its messaging pillars, persona, and proof
  points like "$79/month"), and using it for another site put one brand's
  claims into another brand's articles.
* Article scoring measures internal links against **the site's own domain**,
  not `settings.SUCCESSIFIER_DOMAIN`.

### Ops scripts

| Script | Purpose |
|--------|---------|
| `scripts/audit_content_pipeline.py` | Report/repair the idea→draft→publish chain across all tenants. `--fix` syncs stale-published plan rows and resets stuck `drafting`. |
| `scripts/content_flow_se.py` | Per-tenant reset + refill. `--archive-backlog` archives stale unpublished plan items/pieces (reversible; never touches published rows); `--fill-forward N` gap-fills the next N calendar days via the autopilot pipeline. Dry-run by default; `--apply` to write. Identify the tenant with `--tenant <site_id>` or `--domain successifier.se`. |

Both need the backend env (Supabase service role; `content_flow_se.py --fill-forward`
also needs `ANTHROPIC_API_KEY`). Content is keyed under `tenant_id = site_id`.


## Service-to-service auth (`SAMA_INTERNAL_TOKEN`)

The tenant middleware only trusts header-based tenant context
(`X-Sama-Account-Id` / `X-Sama-Site-Id` without a Supabase JWT) from callers
presenting `X-Sama-Internal-Token: {SAMA_INTERNAL_TOKEN}` — the dashboard
proxy, its cron routes, and the publish bridge all send it. Configure the
same value on Railway and Vercel. While the env var is unset the middleware
runs in a logged migration mode that still trusts bare headers (so existing
deployments keep working), but every such request is warned about. The token
also gates the `/api/dev-agent/*` FORGE endpoints (fail closed: without the
env var they are disabled) and marks OAuth/tenant-sensitive calls as trusted.

Related env flags: `STRICT_SITE_VALIDATION` (site↔account ownership check for
JWT callers, **on by default**, set `0` to disable), `BACKEND_PAUSED=1`
(emergency 503 for all `/api/*` traffic), `BREVO_WEBHOOK_SECRET` (shared
token for the Brevo webhook, sent as `?token=` or `X-Brevo-Token`).
