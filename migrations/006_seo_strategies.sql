-- SEO Strategies Table
-- Stores generated SEO strategies with tasks and data fingerprints

CREATE TABLE IF NOT EXISTS seo_strategies (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  headline TEXT NOT NULL,
  strategy_json JSONB NOT NULL,
  tasks JSONB NOT NULL DEFAULT '[]'::jsonb,
  data_fingerprint TEXT NOT NULL,
  ranked_keywords_count INTEGER DEFAULT 0,
  total_keywords_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Only need latest strategy, but keep history
CREATE INDEX idx_seo_strategies_created ON seo_strategies(created_at DESC);

COMMENT ON TABLE seo_strategies IS 'AI-generated SEO strategies with task checklists';
COMMENT ON COLUMN seo_strategies.data_fingerprint IS 'Hash of keyword positions used to detect significant data changes';
COMMENT ON COLUMN seo_strategies.tasks IS 'Array of {id, title, category, done, created_at, done_at}';
