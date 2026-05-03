-- Analysis runs (P2.9 SEO + GEO unified analysis) and approval queue
-- (gates Content/Social auto-publishes when tenants opt out of automation).

-- ── Analysis runs ────────────────────────────────────────────────────────────
-- Each row is one full multi-query × multi-platform analysis. The full result
-- payload (overview + per-query matrix + gap categories) is stored as JSONB so
-- the frontend can render historical runs without joining N child tables.
CREATE TABLE IF NOT EXISTS analysis_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL DEFAULT 'default',
  brand_name TEXT,
  domain TEXT,
  query_count INT NOT NULL DEFAULT 0,
  platform_count INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running', -- 'running' | 'completed' | 'failed'
  -- Full AnalysisRun payload (matches the TS type in the dashboard).
  payload JSONB,
  error TEXT,
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_tenant ON analysis_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_started ON analysis_runs(started_at DESC);

-- ── Pending approvals ────────────────────────────────────────────────────────
-- When a tenant has auto_publish_* set to false, the content/social agent
-- writes the draft here and marks status='pending'. The /c/approvals UI lets
-- a human approve, edit, or reject before publication.
CREATE TABLE IF NOT EXISTS pending_approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL DEFAULT 'default',
  -- 'content' | 'social' | 'reviews_response' | other future kinds
  kind TEXT NOT NULL,
  -- Subkind for the agent (e.g. 'blog_post', 'twitter', 'linkedin', 'review_response')
  channel TEXT,
  agent_name TEXT,
  title TEXT,
  body TEXT,
  -- Anything else the agent needs to remember when the human approves —
  -- e.g. the GitHub PR target, the social media account, scheduled time.
  metadata JSONB,
  -- 'pending' | 'approved' | 'rejected' | 'published' | 'failed'
  status TEXT NOT NULL DEFAULT 'pending',
  created_by_agent_run UUID,
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  reviewer_note TEXT,
  published_at TIMESTAMPTZ,
  publish_error TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pending_approvals_tenant_status
  ON pending_approvals(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_created
  ON pending_approvals(created_at DESC);
