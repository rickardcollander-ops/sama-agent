-- SAMA 2.0 - Database Setup
-- Run this in Supabase Dashboard → SQL Editor

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Keywords table
CREATE TABLE IF NOT EXISTS keywords (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword VARCHAR(255) NOT NULL,
    intent VARCHAR(50),
    priority VARCHAR(10),
    target_page VARCHAR(500),
    current_position INTEGER,
    current_clicks INTEGER DEFAULT 0,
    current_impressions INTEGER DEFAULT 0,
    current_ctr FLOAT DEFAULT 0.0,
    position_history JSONB DEFAULT '[]'::jsonb,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    auto_discovered BOOLEAN DEFAULT FALSE
);

-- SEO Audits table
CREATE TABLE IF NOT EXISTS seo_audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_date TIMESTAMPTZ DEFAULT NOW(),
    critical_issues JSONB DEFAULT '[]'::jsonb,
    high_issues JSONB DEFAULT '[]'::jsonb,
    medium_issues JSONB DEFAULT '[]'::jsonb,
    low_issues JSONB DEFAULT '[]'::jsonb,
    lcp_score FLOAT,
    inp_score FLOAT,
    cls_score FLOAT,
    total_pages INTEGER,
    pages_with_issues INTEGER,
    broken_links INTEGER,
    duplicate_content INTEGER,
    auto_fixed JSONB DEFAULT '[]'::jsonb,
    summary TEXT,
    recommendations JSONB DEFAULT '[]'::jsonb
);

-- Content Pieces table
CREATE TABLE IF NOT EXISTS content_pieces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    content_type VARCHAR(50),
    content TEXT,
    meta_title VARCHAR(200),
    meta_description VARCHAR(500),
    target_keyword VARCHAR(255),
    target_url VARCHAR(500),
    word_count INTEGER,
    status VARCHAR(50) DEFAULT 'draft',
    published_at TIMESTAMPTZ,
    impressions_30d INTEGER DEFAULT 0,
    clicks_30d INTEGER DEFAULT 0,
    avg_position FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by VARCHAR(50) DEFAULT 'sama_content',
    approved_by VARCHAR(100)
);

-- Backlink Profiles table
CREATE TABLE IF NOT EXISTS backlink_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    total_backlinks INTEGER,
    referring_domains INTEGER,
    domain_rating FLOAT,
    toxic_links JSONB DEFAULT '[]'::jsonb,
    new_links JSONB DEFAULT '[]'::jsonb,
    lost_links JSONB DEFAULT '[]'::jsonb,
    top_domains JSONB DEFAULT '[]'::jsonb
);

-- Competitor Analyses table
CREATE TABLE IF NOT EXISTS competitor_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor VARCHAR(100) NOT NULL,
    analyzed_at TIMESTAMPTZ DEFAULT NOW(),
    keyword_gaps JSONB DEFAULT '[]'::jsonb,
    keyword_wins JSONB DEFAULT '[]'::jsonb,
    estimated_traffic INTEGER,
    domain_rating FLOAT,
    content_opportunities JSONB DEFAULT '[]'::jsonb
);

-- Enable Row Level Security (allow all for service key)
ALTER TABLE keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE seo_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_pieces ENABLE ROW LEVEL SECURITY;
ALTER TABLE backlink_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_analyses ENABLE ROW LEVEL SECURITY;

-- Create policies for service role access
CREATE POLICY "Allow all for service role" ON keywords FOR ALL USING (true);
CREATE POLICY "Allow all for service role" ON seo_audits FOR ALL USING (true);
CREATE POLICY "Allow all for service role" ON content_pieces FOR ALL USING (true);
CREATE POLICY "Allow all for service role" ON backlink_profiles FOR ALL USING (true);
CREATE POLICY "Allow all for service role" ON competitor_analyses FOR ALL USING (true);
