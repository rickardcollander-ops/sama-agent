-- Marketing Strategy storage
-- Holds strategies produced by the StrategyAgent: a unified, cross-channel
-- plan synthesised from each domain agent's latest activity & reports.

CREATE TABLE IF NOT EXISTS marketing_strategies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL DEFAULT 'default',
  generated_at TIMESTAMPTZ DEFAULT now(),
  -- Headline summary fields
  headline TEXT,
  verdict TEXT,           -- one of: 'critical', 'weak', 'improving', 'strong'
  horizon TEXT DEFAULT 'quarterly',  -- 'monthly' | 'quarterly' | 'annual'
  -- Full structured strategy as JSON: per-domain analysis, priorities,
  -- cross-channel synergies, roadmap, KPIs.
  strategy JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Snapshot of which agents contributed (so we can show which were enabled
  -- when this strategy was produced).
  contributing_agents TEXT[] DEFAULT ARRAY[]::TEXT[],
  status TEXT DEFAULT 'active',  -- 'active' | 'archived'
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_marketing_strategies_tenant
  ON marketing_strategies(tenant_id);
CREATE INDEX IF NOT EXISTS idx_marketing_strategies_generated
  ON marketing_strategies(generated_at DESC);

-- Register the strategy agent in tenant_agent_config so it can be toggled
-- alongside the other agents. Enabled by default for the home tenant.
INSERT INTO tenant_agent_config (tenant_id, agent_name, enabled, schedule) VALUES
  ('default', 'strategy', true, 'weekly')
ON CONFLICT (tenant_id, agent_name) DO NOTHING;
