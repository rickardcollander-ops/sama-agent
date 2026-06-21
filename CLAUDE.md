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

**Daily cron** — runs every day 06:00 Europe/Stockholm for ALL onboarded users:

```json
{
  "source": "daily_cron",
  "ideas_per_run": 1,
  "auto_draft_top_n": 1,
  "auto_publish": false,
  "scheduled_for_days_ahead": 2
}
```

Intent: generate 1 idea and write the article for the day after tomorrow. This is the core
rolling flow that keeps the content calendar continuously filled.

**Weekly autopilot** — runs every Monday 07:30 Europe/Stockholm, only for users with
`user_settings.settings.content_autopilot.enabled = true`:

```json
{
  "source": "weekly_cron",
  "ideas_per_run": 6,
  "auto_draft_top_n": 3,
  "auto_publish": false,
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
   `auto_publish_on_schedule = true`. **Do not publish here.**
4. **Otherwise (review-first mode, or score below threshold):** insert into `approvals`
   with `status = "pending"`. Approving in `/c/approvals` flips the piece to
   `status = "approved"` and sets `scheduled_for = now`.
5. Return immediately with `{ run_id, status: "running" }` — work happens async

> **Publishing is owned by the dashboard, not the backend.** The backend's job ends at
> generate → draft → schedule/approve. The dashboard's 5-min publish cron
> (`/api/integrations/cron` → auto-publish bridge) ingests pieces whose `piece_status`
> is `"approved"` with a due `scheduled_for` and ships them to that tenant's own
> destination (CMS or GitHub). The old hardcoded-GitHub publish in
> `process_due_scheduled_items` is disabled to avoid double-publishing.

> **Critical:** `scheduled_for_days_ahead: 2` pins the article to the correct calendar
> date. A fully-automatic piece auto-publishes on that date; a review-first piece
> publishes within ~5 min of approval.
