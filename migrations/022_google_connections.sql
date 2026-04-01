-- Google OAuth connections per tenant
CREATE TABLE IF NOT EXISTS google_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL DEFAULT 'default',
  service TEXT NOT NULL, -- 'search_console' | 'analytics' | 'ads'
  access_token TEXT,
  refresh_token TEXT,
  token_expiry TIMESTAMPTZ,
  scopes TEXT,
  connected_at TIMESTAMPTZ DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, service)
);

CREATE INDEX IF NOT EXISTS idx_google_connections_tenant ON google_connections(tenant_id);
