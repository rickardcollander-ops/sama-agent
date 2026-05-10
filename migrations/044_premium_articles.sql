-- 044_premium_articles.sql
-- Adds the columns the new "premium" article writer needs so the dashboard
-- can render structured articles (TOC, key takeaways, FAQ, scored
-- optimisation, hybrid imagery, internal/external links).
--
-- All columns are nullable / JSONB so existing rows are unaffected. The
-- writer always populates ``article_data`` with the full structured payload
-- — the top-level columns are denormalised mirrors that let the dashboard
-- list query stay cheap (slug for routing, featured_image_url for cards,
-- score for ordering).

ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS slug                TEXT,
    ADD COLUMN IF NOT EXISTS featured_image_url  TEXT,
    ADD COLUMN IF NOT EXISTS featured_image_alt  TEXT,
    ADD COLUMN IF NOT EXISTS article_score       INT,
    ADD COLUMN IF NOT EXISTS article_data        JSONB;

CREATE INDEX IF NOT EXISTS idx_content_pieces_slug
    ON content_pieces (slug)
    WHERE slug IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_pieces_score
    ON content_pieces (article_score DESC NULLS LAST);
