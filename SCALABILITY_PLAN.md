# SAMA — Skalbarhetsplan

Ägare: Plattformsteamet · Status: Förslag · Senast uppdaterad: 2026-05-05

Denna plan täcker **båda repon** (`sama-dashboard` och `sama-agent`). En identisk
kopia ligger i `sama-agent/SCALABILITY_PLAN.md` så att teamen kan referera till
samma punkter oavsett vilket repo de arbetar i.

---

## 1. Sammanfattning

SAMA består idag av två tjänster som fungerar utmärkt för en handfull tenants
men har inbyggda flaskhalsar som börjar klämma åt redan vid 50–100 aktiva
kunder och som kommer att brista mellan 200–500.

De fem viktigaste hindren mot horisontell skalning:

1. **Allt körs i samma FastAPI-process** — APScheduler, agent monitor loop,
   event bus och HTTP-trafik delar event loop. Två replicas → schemajobb körs
   två gånger.
2. **Synkron Supabase-klient i async hot paths** — varje
   `sb.table().execute()` blockerar event loop. CPU-tråden hostar bort
   genomströmning så fort vi går över ~10 samtidiga requests per process.
3. **Tenant fan-out via `asyncio.create_task`** — schemaläggaren spawnar
   obegränsat antal samtidiga LLM-cykler i en process. Vid 100 tenants ×
   6 agenter = 600 samtidiga Anthropic-anrop från en pod.
4. **Två motstridiga deploys** — backend deployas både på Railway (riktig
   process med `lifespan`) och Vercel (`api/index.py` med Mangum
   `lifespan="off"`, så scheduler/monitor körs aldrig där).
5. **Vercel-cron driver allt schemaarbete i dashboard** — `*/5 * * * *`
   skannar `user_settings.settings.scheduled_publishes` (JSONB-array) per
   tenant. O(N) per körning, plus skriv-konflikter när två agenter samtidigt
   uppdaterar samma JSONB.

Resten av dokumentet listar konkreta åtgärder per nivå (P0 = blockerande för
skalning, P1 = stabilitet, P2 = effektivitet) med vilka filer som måste ändras.

---

## 2. Kontext — vad finns idag

### sama-dashboard (Next.js 16 / Vercel / Supabase)
- App Router, kund-portal `/c/*` (Supabase Auth), admin `/*` (cookie
  `MISSION_SECRET`).
- `proxy.ts` middleware verifierar Supabase-session vid varje `/c/*`-request.
- `lib/api.ts` proxar via `/api/sama/[...path]` till backend.
- Vercel cron `*/5 * * * *` triggar `/api/integrations/cron` som skannar
  alla tenants efter due `scheduled_publishes` i `user_settings.settings`.
- Supabase används för auth, `user_settings` (JSONB-blob), `public_audits`,
  `audit_leads`.
- Public audit-sidan har världsläsbar tabell (`public_audits`) — hittas via
  URL-id.

### sama-agent (FastAPI / Python / Railway + Vercel)
- En enda FastAPI-app (`main.py`) med ~50 routers under `api/routes/*`.
- `lifespan` startar: APScheduler, event bus (Redis eller in-process fallback),
  agent monitor loop, agent chains.
- 28 agentmoduler i `agents/*` — flera över 30k–48k tecken (`seo.py`,
  `analytics.py`, `site_audit.py`, `content.py`).
- Multi-tenant via `tenant_id`-kolumn + RLS på de centrala tabellerna
  (migrationer 020–029).
- Service-role-nyckel används överallt på backend → bypassar RLS, men det är
  RLS-policyn som är säkerhetsbarriären om någon nyckel läcker.
- Tenant fan-out i `shared/scheduler.py::_run_for_all_tenants` läser
  `tenant_agent_config` och `asyncio.create_task(_execute_run(...))` per tenant.
- Watchdog: `agent_runs` med `started_at < now - 15min` markeras som failed.
- In-memory rate limiter (`shared/rate_limiter.py`) — fungerar bara per process.

### Datalager (Supabase Postgres)
- ~25 migrationer.
- Vissa tabeller har RLS, andra `Allow all for service role` (otillräcklig
  isolering om service-key läcker — t.ex. `agent_actions`, `chat_history`,
  `agent_learnings`).
- `user_settings.settings` är ett stort JSONB-objekt som lagrar plan,
  publishing destinations, schemalagda publiceringar, GEO-queries,
  brand voice, API-nycklar mm. Skrivkollisioner är garanterade vid skalning.

---

## 3. Skalbarhetsflaskhalsar — P0 (måste lösas innan tillväxt)

### P0-1. Bryt ut bakgrundsarbete från web-processen
**Symptom:** En enda Python-process kör HTTP, scheduler, monitor loop och
LLM-cykler. Replicas duplicerar jobb. Vercel-deploy kör inte bakgrund alls.

**Åtgärd:**
- Inför en dedikerad worker-process (eget repo-mål eller separat Procfile-rad).
- Byt APScheduler mot **Arq** eller **Celery + Redis** (Arq är lätt och
  asynkront, passar FastAPI). Alternativ: **Temporal** (redan i config) för
  durable workflows när vi når komplexitet.
- Web-processen ska bara: ta emot HTTP, validera, lägga jobb i kö, returnera
  202 + run_id.
- Ta bort `asyncio.create_task(_execute_run(...))` i
  `api/routes/tenant_activation.py:trigger_agent` — ersätt med
  `await queue.enqueue("execute_run", run_id, tenant_id, agent_name)`.
- Scheduler-loopen i `shared/scheduler.py` ersätts av cron-jobb i samma kö.
- Vinst: web-processer blir stateless → autoskala fritt.

**Filer:**
- `sama-agent/main.py` (ta bort scheduler/monitor/event_bus från `lifespan`)
- `sama-agent/shared/scheduler.py` (ersätt med kö-jobb)
- `sama-agent/api/routes/tenant_activation.py` (`trigger_agent`, `_execute_run`)
- Nytt: `sama-agent/worker.py`, `sama-agent/shared/queue.py`

### P0-2. Sluta blockera event loop med synkron Supabase-klient
**Symptom:** `supabase-py` är synkron. Varje `sb.table(...).execute()` i en
async-funktion blockerar event-loopen. Vid 50 RPS hänger en pod sig.

**Åtgärd:**
- Migrera DB-åtkomsten till **`asyncpg`** direkt mot Supabase Postgres-port
  (eller `psycopg[async]`), eller wrappa supabase-anropen i
  `asyncio.to_thread`.
- Inför en connection pool (`asyncpg.create_pool`) i `shared/database.py`.
- Kör mot **Supabase pgbouncer-port (6543)** för transaction pooling.
- Bevara Supabase REST endast för auth, storage, realtime — inte för
  applikationsdata.

**Filer:**
- `sama-agent/shared/database.py`
- Alla `sb.table(...).execute()`-anrop (~80+ ställen)
- `sama-agent/requirements.txt` (lägg till asyncpg)

### P0-3. Begränsa fan-out concurrency per tenant och globalt
**Symptom:** `_run_for_all_tenants` skapar ett `asyncio.create_task` per
tenant utan tak. Vid 200 tenants = 200 LLM-cykler samtidigt → Anthropic
quota brinner, OOM.

**Åtgärd:**
- Inför `asyncio.Semaphore(N)` med N från config (t.ex. 10).
- När vi flyttar till kö (P0-1) hanteras detta naturligt av worker-poolen.
- Per-tenant samtidighet: max 1 körning per `(tenant_id, agent_name)`. Använd
  Postgres advisory locks eller Redis SET NX för det.

**Filer:**
- `sama-agent/shared/scheduler.py:_run_for_all_tenants`
- `sama-agent/api/routes/tenant_activation.py:trigger_agent`

### P0-4. Välj EN backend-deploy och städa bort den andra
**Symptom:** `vercel.json` + `api/index.py` (Mangum) och `railway.json` +
`start.py` finns båda. Vercel-versionen kör inte `lifespan` →
scheduler/monitor/event_bus startas aldrig där. Cron i `vercel.json` försöker
ringa endpoints som inte existerar (`/api/automation/daily-workflow`).

**Åtgärd:**
- Behåll Railway för backend (eller flytta till Fly.io / Render / GCP Cloud
  Run). Ta bort Mangum-stub:
  - `sama-agent/api/index.py`
  - `sama-agent/vercel.json`
  - `sama-agent/requirements-vercel.txt`
- Behåll Vercel för dashboard (Next.js).
- Dashboard-cron i `sama-dashboard/vercel.json` ska bara trigga
  `/api/integrations/cron` som proxar vidare till backend.

### P0-5. Bryt ut `user_settings.settings.scheduled_publishes` ur JSONB
**Symptom:** Vercel cron `*/5 * * * *` läser hela `user_settings`-raden för
varje tenant, parsar JSONB, filtrerar due, skriver tillbaka. Blir O(N×M) och
har race conditions.

**Åtgärd:**
- Migrera till en dedikerad tabell:
  ```sql
  CREATE TABLE scheduled_publishes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    piece_id text NOT NULL,
    destination_id text NOT NULL,
    scheduled_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'scheduled',
    payload jsonb,
    published_url text,
    error text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
  );
  CREATE INDEX idx_scheduled_publishes_due
    ON scheduled_publishes (scheduled_at)
    WHERE status = 'scheduled';
  ```
- Cron-handlern blir då en enkel `WHERE status='scheduled' AND
  scheduled_at <= now() LIMIT 50 FOR UPDATE SKIP LOCKED`-loop. Inga JSONB-
  parser-kostnader, ingen tenant-fan-out i koden.
- Samma struktur för `publishing_destinations` (egen tabell).

**Filer:**
- `sama-dashboard/lib/integrations/store.ts`
- `sama-dashboard/app/api/integrations/cron/*`
- Ny migration i `sama-agent/migrations/030_publishing_tables.sql`

### P0-6. Distribuerad rate-limiting och budget-guards för Anthropic
**Symptom:** `shared/rate_limiter.py` är in-memory → fel värden under
horisontell skalning. Anthropic-nyckeln delas globalt utan per-tenant cap.

**Åtgärd:**
- Implementera token-bucket i Redis (eller använd `aiolimiter` + Redis-state).
- Spåra per-tenant token-förbrukning i `tenant_usage`-tabellen (finns redan
  delvis i migration 024). Lägg till `metric='anthropic_input_tokens'` och
  `'anthropic_output_tokens'`.
- När en tenant överstiger budget → returnera 402 i stället för att brännga
  pengar. Stoppa även cron-jobb när cap nås.

**Filer:**
- `sama-agent/shared/rate_limiter.py` (Redis-backat)
- `sama-agent/shared/usage.py` (lägg till token-metrics)
- Alla LLM-anrop bör gå genom en wrapper `shared/llm.py` med:
  - prompt caching (`anthropic-beta: prompt-caching-2024-07-31`)
  - retries med exponential backoff
  - automatisk modellnedgradering vid quota
  - per-tenant token-loggning

### P0-7. Höj watchdog-tröskeln och flytta till durable workflow
**Symptom:** `STALE_RUN_AFTER = 15min`. Riktiga LLM-cykler (t.ex. content +
publishing till 4 destinations) kan ta längre. Stale-reaper markerar dem
felaktigt som failed.

**Åtgärd:**
- Höj till 60 min, eller bättre: ta bort tröskel-baserad reaper helt och
  använd worker-heartbeats. Med kö-systemet (P0-1) får vi det gratis: jobb
  som inte heartbeatar inom T blir omkörda.
- Logga `last_heartbeat_at` i `agent_runs`.

---

## 4. P1 — viktiga för stabilitet i produktion

### P1-1. Skydda public-audit och audit-leads mot abuse
- `public_audits` är världsläsbar — det är okej för delning men exponerar
  också full audit-payload. Tillåt bara `select` på `id, domain, payload,
  created_at` (inte ip_hash).
- Inför rate limit (Cloudflare WAF / nginx / Vercel Edge Config) på
  `/audit` och `/api/public-audit/*`. En audit kostar Anthropic-tokens.
- `audit_leads`: lägg till captcha (Cloudflare Turnstile) eller hCaptcha på
  formuläret innan email skrivs.

### P1-2. Idempotency keys på publishing och social posts
- Klienten genererar UUID per "publish"-anrop. Backend ignorerar duplikater
  inom 24h. Skyddar mot retry-storm vid nätverksfel.

### P1-3. Distribuerade locks för cron-jobb
- Om scheduler råkar spinna upp i två replicas körs `weekly_seo_audit` två
  gånger. Använd `pg_advisory_lock(hashtext('weekly_seo_audit'))` runt varje
  cron-handler, eller Redis SET NX.

### P1-4. Tenant-cache-invalidering över replicas
- `shared/tenant.py` har `_tenant_cache` per process. Användarens
  /c/settings-edit invaliderar bara den ena replikan.
- Lös via Redis pub/sub `tenant:invalidate` eller minska TTL till 30s.

### P1-5. RLS-täckning för alla tabeller
- Migration 013 är för tillåten ("Allow all for service role" på
  `agent_actions`, `chat_history`, `agent_learnings`, `agent_goals`,
  `ai_visibility_*`). Lägg till tenant_id-filter precis som migration 020.
- Mål: zero-trust om service-keyn läcker.

### P1-6. Observability — Sentry + Datadog faktiskt påkopplat
- `SENTRY_DSN` och `DATADOG_API_KEY` finns i config men ingen import.
  Lägg till `sentry_sdk.init(...)` i `main.py` och Sentry-init i
  `app/layout.tsx` för dashboard.
- Strukturerad loggning (JSON) → Datadog logs.
- Per-tenant trace-tags så vi kan följa en kund genom hela kedjan.

### P1-7. Plan-billing via Stripe
- `shared/usage.py` har plan-limits men inget billing-flöde. `/c/pricing`
  CTAs leder till `/c/login`. Koppla in Stripe Customer Portal för
  uppgraderingar; webhook synkar `user_settings.settings.plan`.

### P1-8. API-versionering på backend
- `/api/v1/*` så vi kan göra brytande ändringar utan att slå ut alla
  tenants. Idag bryter en endpoint-rename hela dashboarden.

### P1-9. SSE/WebSocket för agent_runs istället för polling
- `lib/api.ts:pollAgentRun` polar var 3 sek i upp till 15 min. Vid 100
  samtidiga kunder × 6 agenter = ~12k requests/min mot proxy. Byt mot
  Server-Sent Events från `/api/tenant/agent-runs/{id}/stream`.

---

## 5. P2 — effektivitet och kvalitet

### P2-1. Prompt caching för Anthropic
- Systempromptar och brand context (5–10k tokens) skickas på varje
  agent-anrop. Aktivera prompt caching → 80–90% rabatt på upprepade prompts.
  Använd `cache_control: {type: "ephemeral"}` på systemmeddelandet.

### P2-2. Caching-lager mellan dashboard och backend
- HTTP-cache headers (`Cache-Control: s-maxage=60, stale-while-revalidate=300`)
  på GET-endpoints som inte är tenant-personliga.
- Edge cache via Vercel för `/audit/r/{id}` (publika audits).
- React Query / SWR i dashboard för dedup + revalidation.

### P2-3. Konsolidera Supabase-klienter i dashboard
- Idag finns `lib/supabase.ts`, `lib/supabase-browser.ts`,
  `lib/supabase-server.ts`. Ta bort `supabase.ts` (legacy) och dokumentera
  vilken som ska användas var.

### P2-4. Bryt ner megamoduler
- `agents/seo.py` (47k), `agents/analytics.py` (49k), `agents/site_audit.py`
  (38k), `agents/content.py` (34k) är för stora för rimligt code review.
- Föreslagen uppdelning: `seo/keywords.py`, `seo/audit.py`, `seo/serp.py`,
  `seo/competitors.py` etc.

### P2-5. Background streaming för CSV/PDF-export
- Idag genereras export inline → request timeout på Vercel (10s default).
  Lägg jobben i kö, skicka mail med presigned URL när klart.

### P2-6. CDN för publika sidor
- `/c/pricing`, `/c/audit` ska serveras från CDN, inte hit:a Next.js varje
  gång.

### P2-7. Konsolidera deployment till en plattform
- Antingen alla i Vercel + extern worker (Railway/Render), eller alla i en
  cloud (Fly.io). Underhåll av två olika env-filer (`.env.example` +
  `.env.local.template` + Vercel Env + Railway Vars) är en buggfälla.

### P2-8. End-to-end-tester för tenant-isolering
- Pytest-svit som loggar in som tenant A, hittar resurs, försöker komma åt
  som tenant B → måste få 404. Körs i CI på varje PR.

### P2-9. Pre-aggregera dashboards
- `/api/dashboard/status` gör 5+ DB-queries per request. Lägg till en
  `tenant_dashboard_cache`-tabell som uppdateras async av en bakgrundsjobb,
  serveras direkt vid GET.

---

## 6. Kapacitetsmål och konkreta gränser

| Metric | Idag (uppskattat) | Efter P0 | Efter P0+P1 |
|---|---|---|---|
| Aktiva tenants | 5–20 | 200 | 2 000 |
| RPS per backend-pod | ~10 | ~200 | ~500 |
| Schedulerade publiceringar / dygn | 100 | 10 000 | 100 000 |
| Samtidiga LLM-cykler per pod | obegränsat | 10 (semaphore) | N/A (kö) |
| p95 dashboard-latens | 1 200 ms | 400 ms | 200 ms |
| Anthropic-spend / tenant / mån | okontrollerat | hard cap per plan | hard cap + larm |

---

## 7. Föreslagen tidslinje

**Sprint 1 (vecka 1–2) — släck branden**
- P0-2 (asyncpg + thread-poolad supabase)
- P0-4 (rensa Vercel-deploy av backend)
- P0-7 (höj watchdog-tröskel)
- P1-3 (advisory locks för cron)
- P1-6 (Sentry på)

**Sprint 2 (vecka 3–4) — ut ur web-processen**
- P0-1 (Arq-worker + kö)
- P0-3 (concurrency-limit)
- P1-1 (rate-limit public-endpoints)

**Sprint 3 (vecka 5–6) — datamodell**
- P0-5 (scheduled_publishes-tabell)
- P0-6 (Redis rate-limiter + token-budget)
- P1-5 (RLS överallt)

**Sprint 4 (vecka 7–8) — produktklass**
- P1-2, P1-7, P1-8, P1-9
- P2-1 (prompt caching) — billigaste vinsten av allt

---

## 8. Risker

- **Datamigrering av JSONB → tabell** (P0-5) kräver dual-write under en
  period. Plan: skriv till båda i 2 veckor, läs från ny tabell, ta bort
  JSONB-fältet sist.
- **Bytet till asyncpg** (P0-2) ändrar transaktionssemantik; täck med tester
  innan deploy.
- **Worker-migration** (P0-1) — kör befintlig scheduler parallellt med Arq i
  shadow-mode tills jobben matchar.
- **RLS-skärpning** (P1-5) — kan bryta admin-flöden som läste cross-tenant
  med service-key. Audit alla skrivningar innan vi tar bort
  "Allow all for service role".

---

## 9. Öppna frågor till teamet

1. Vill vi behålla Supabase Auth eller flytta till Auth.js / Clerk i
   samband med ombyggnaden? Påverkar JWT-signering i RLS.
2. Är Temporal övermäktigt för use case-storleken, eller ska vi sikta dit
   direkt? Arq är enklare men mindre durable.
3. Pricing-tier för Anthropic — är vi fortfarande på Tier 1 (50 RPM)? Det
   är en hård gräns redan vid 50 tenants.
4. Ska public audits flyttas till en helt separat origin (t.ex.
   `audit.successifier.com`) så DDoS där inte påverkar produkten?
