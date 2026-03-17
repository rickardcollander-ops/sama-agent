-- Migration 012: Add review_responses and notifications tables
-- These tables are referenced by the review agent and notification service
-- but were never created in the database schema.

-- Reviews table (stores fetched reviews from all platforms)
CREATE TABLE IF NOT EXISTS reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(50) NOT NULL,
    rating INTEGER,
    author VARCHAR(255),
    title TEXT,
    content TEXT,
    review_url TEXT,
    responded BOOLEAN DEFAULT FALSE,
    response_text TEXT,
    responded_at TIMESTAMPTZ,
    sentiment VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reviews_platform ON reviews(platform);
CREATE INDEX IF NOT EXISTS idx_reviews_responded ON reviews(responded);
CREATE INDEX IF NOT EXISTS idx_reviews_created_at ON reviews(created_at DESC);

-- Review responses table (stores generated/published responses)
CREATE TABLE IF NOT EXISTS review_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id UUID REFERENCES reviews(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    sentiment VARCHAR(20),
    response_text TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'draft',
    approved_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_responses_review_id ON review_responses(review_id);
CREATE INDEX IF NOT EXISTS idx_review_responses_status ON review_responses(status);

-- Social posts table (stores generated/published social media posts)
CREATE TABLE IF NOT EXISTS social_posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(50) NOT NULL DEFAULT 'twitter',
    content TEXT NOT NULL,
    content_type VARCHAR(50) DEFAULT 'post',
    topic VARCHAR(255),
    style VARCHAR(50),
    status VARCHAR(20) DEFAULT 'draft',
    scheduled_for TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    engagement_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_posts_status ON social_posts(status);
CREATE INDEX IF NOT EXISTS idx_social_posts_platform ON social_posts(platform);

-- Notifications table (stores dashboard notifications)
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    severity VARCHAR(20) DEFAULT 'info',
    agent VARCHAR(50) DEFAULT 'system',
    fields JSONB DEFAULT '{}'::jsonb,
    read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    read_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_agent ON notifications(agent);

-- Enable realtime for notifications (required for Supabase Realtime subscriptions)
ALTER PUBLICATION supabase_realtime ADD TABLE notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE reviews;
ALTER PUBLICATION supabase_realtime ADD TABLE review_responses;
ALTER PUBLICATION supabase_realtime ADD TABLE social_posts;

-- Alerts table (referenced by dashboard realtime subscription)
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    message TEXT,
    severity VARCHAR(20) DEFAULT 'info',
    agent VARCHAR(50),
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
ALTER PUBLICATION supabase_realtime ADD TABLE alerts;
