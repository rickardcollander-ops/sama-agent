# SAMA Threat Model

_Last updated: 2026-05-08. Owner: rc@successifier.com._

## Trust boundaries

```
                    ┌─────────────────┐
   end user ──TLS──▶│  Vercel (Next)  │──TLS──┐
                    │  sama-dashboard │       │
                    └─────────────────┘       ▼
                                       ┌─────────────────┐
              service-role ───────────▶│ Railway (FastAPI)│
                                       │   sama-agent    │
                                       └────────┬────────┘
                                                │
                              ┌─────────────────┼──────────────────┐
                              ▼                 ▼                  ▼
                          Supabase           Redis           Anthropic, GA, Ads
                         (Postgres)        (events)          (3rd-party APIs)
```

## Asset register (rough priority)

| Asset                          | Sensitivity | Stored where                   |
| ------------------------------ | ----------- | ------------------------------ |
| Customer Google/Twitter tokens | high        | `user_settings.settings_encrypted` |
| Anthropic API key (operator)   | high        | env (Railway secret)           |
| Supabase service-role          | very high   | env                            |
| Tenant content (drafts, plans) | medium      | various Supabase tables        |
| Audit logs                     | medium      | `audit_log` (append-only)      |

## Known controls

| # | Threat                                       | Control(s)                                         |
| - | -------------------------------------------- | -------------------------------------------------- |
| 1 | Cross-tenant data access                     | Server-side tenant validation in dashboard proxy + JWT-verified `tenant_middleware` + RLS on all tenant tables |
| 2 | Auth-cookie theft / fixation                 | Supabase Auth cookies (httpOnly, Secure, SameSite=Lax); admin-only routes gated by `requireAdmin()` |
| 3 | Prompt injection in user-supplied LLM input  | `wrap_user_content` + `prompt_guard.scan` in orchestrator, system-prompt sandboxing |
| 4 | SSRF via scrapers                            | `shared/safe_http.py` IP-allowlist + redirect re-validation |
| 5 | DoS via public audit                         | IP rate limit + Cloudflare Turnstile (when enabled) |
| 6 | Secrets at rest in `user_settings`           | Envelope encryption (NaCl secretbox) via `MASTER_KMS_KEY` |
| 7 | Webhook spoofing                             | HMAC verification (`shared/webhook_verify.py`) for Cal.com / Stripe / GitHub / Slack |
| 8 | Cost runaway via runaway LLM calls           | `llm_budget` (input cap + output cap) + `llm_pool` semaphores + `usage` per-tenant monthly quotas |

## Open gaps (tracked elsewhere)

- Per-tenant audit-log dashboard for customers ("who changed what").
- Multi-region failover (RPO/RTO targets undefined).
- Bring `mypy` into CI as gating instead of advisory.
- Quarterly external penetration test.

## Incident response

1. **Detect** — Sentry alerts, Datadog anomaly, customer report.
2. **Triage** — file an incident channel; pull responder rota.
3. **Contain** — `BACKEND_PAUSED=1` to halt agent traffic; revoke compromised credential at provider.
4. **Eradicate** — patch + deploy.
5. **Recover** — verify usage normalises, lift `BACKEND_PAUSED`.
6. **Postmortem** — within 5 business days; root cause + corrective actions; share with customers if material.
