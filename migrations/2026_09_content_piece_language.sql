-- 2026_09_content_piece_language.sql
-- Records the language each article was actually written in.
--
-- Content is keyed per site (tenant_id = user_sites.id) and each site has its
-- own language: successifier.com publishes English, successifier.se and
-- supportifier.se publish Swedish. Until now nothing stored the language on the
-- piece itself, so the dashboard's publish bridge fell back to the site's
-- `content_language` setting — and to "en" when that field had never been
-- filled in. A Swedish article could therefore ship to the CMS tagged as
-- English, with `lang="en"` in its JSON-LD.
--
-- Nullable TEXT: existing rows keep NULL and the bridge's existing fallback
-- chain (piece.language -> site content_language -> "en") still applies to them.

ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS language TEXT;

COMMENT ON COLUMN content_pieces.language IS
    'ISO-639-1 code the article body was generated in (e.g. sv, en). Set by the '
    'content autopilot from the site''s content_language; NULL for rows written '
    'before this column existed.';
