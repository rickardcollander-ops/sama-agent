-- Site audits — full-domain SEO + GEO + technical + link health crawl.
-- Each row is one audit of one domain. The full report (overall scores,
-- per-page findings, broken links, recommendations) is stored as JSONB so
-- the dashboard can render historical audits without joining child tables.
CREATE TABLE IF NOT EXISTS site_audits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL DEFAULT 'default',
  domain TEXT,
  pages_analyzed INT NOT NULL DEFAULT 0,
  overall_score INT,
  status TEXT NOT NULL DEFAULT 'running', -- 'running' | 'completed' | 'failed'
  payload JSONB,
  error TEXT,
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_site_audits_tenant ON site_audits(tenant_id);
CREATE INDEX IF NOT EXISTS idx_site_audits_started ON site_audits(started_at DESC);
