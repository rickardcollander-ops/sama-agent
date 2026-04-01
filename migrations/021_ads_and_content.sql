-- 021_ads_and_content.sql
-- Ad platform credentials and ad creatives tables, plus tenant_id on content_pieces

CREATE TABLE IF NOT EXISTS ad_platform_credentials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT DEFAULT 'default',
  platform TEXT NOT NULL,
  access_token TEXT,
  account_id TEXT,
  is_connected BOOLEAN DEFAULT false,
  connected_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, platform)
);

CREATE TABLE IF NOT EXISTS ad_creatives (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT DEFAULT 'default',
  campaign_id UUID,
  platform TEXT NOT NULL,
  format TEXT,
  headline TEXT,
  body_text TEXT,
  cta TEXT,
  image_url TEXT,
  ai_recommendations JSONB,
  performance JSONB,
  is_manual BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Add tenant_id to content_pieces if not exists
ALTER TABLE content_pieces ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'default';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_content_pieces_tenant ON content_pieces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_tenant ON ad_creatives(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ad_credentials_tenant ON ad_platform_credentials(tenant_id);
