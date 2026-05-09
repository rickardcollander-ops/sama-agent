-- Migration 044: external_pages
-- Stores URLs discovered from a tenant's sitemap so the internal-linking
-- optimizer can suggest links to existing site content that wasn't authored
-- by SAMA (legacy posts, product pages, docs, etc.).

CREATE TABLE IF NOT EXISTS external_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL DEFAULT 'default',
    url TEXT NOT NULL,
    title TEXT,
    description TEXT,
    h1 TEXT,
    -- Embedding stored as JSONB array of floats (no pgvector dependency).
    -- Cosine similarity is computed in Python; the candidate set is small
    -- enough (a few hundred URLs per tenant) that exact scan is fine.
    embedding JSONB,
    embedding_model TEXT,
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, url)
);

CREATE INDEX IF NOT EXISTS idx_external_pages_tenant_id
    ON external_pages (tenant_id);

ALTER TABLE external_pages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_external_pages ON external_pages;
CREATE POLICY tenant_isolation_external_pages ON external_pages
    FOR ALL
    USING (tenant_id = COALESCE(current_setting('request.jwt.claim.tenant_id', true), 'default'));
