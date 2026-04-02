-- Agent activation and control tables for multi-tenant support

CREATE TABLE IF NOT EXISTS tenant_agent_config (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL,
  agent_name TEXT NOT NULL, -- 'seo', 'content', 'social', 'ads', 'reviews', 'analytics', 'geo'
  enabled BOOLEAN DEFAULT true,
  schedule TEXT DEFAULT 'daily', -- 'daily', 'weekly', 'manual'
  last_run_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, agent_name)
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL DEFAULT 'default',
  agent_name TEXT NOT NULL,
  status TEXT DEFAULT 'running', -- 'running', 'completed', 'failed'
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ,
  summary TEXT,
  error TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_agent_config_tenant ON tenant_agent_config(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant ON agent_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started ON agent_runs(started_at DESC);

-- Insert default agent configs for the default tenant
INSERT INTO tenant_agent_config (tenant_id, agent_name, enabled, schedule) VALUES
  ('default', 'seo', true, 'daily'),
  ('default', 'content', true, 'weekly'),
  ('default', 'social', true, 'daily'),
  ('default', 'ads', false, 'manual'),
  ('default', 'reviews', true, 'daily'),
  ('default', 'analytics', true, 'daily'),
  ('default', 'geo', true, 'weekly')
ON CONFLICT (tenant_id, agent_name) DO NOTHING;
