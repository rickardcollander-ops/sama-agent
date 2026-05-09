-- Sprint 2 (K-2 / K-5) — track *why* a content piece was created so we can
-- show "Skapad från lucka …" / "Skapad utifrån strategi-topic …" in the UI
-- and later (K-10) close the loop on Insikter when an article is published.
--
-- Both fields are free-form text:
--   * source_gap_id          — opaque id from the Insikter gap surface
--   * source_strategy_topic  — title or id of the strategy topic that
--                              motivated the article
--
-- We don't add a foreign key because the gap surface is currently derived
-- from analysis runs and not a stable table; the id is enough to round-trip.

ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS source_gap_id TEXT,
    ADD COLUMN IF NOT EXISTS source_gap_title TEXT,
    ADD COLUMN IF NOT EXISTS source_strategy_topic TEXT;

CREATE INDEX IF NOT EXISTS idx_content_pieces_source_gap
    ON content_pieces (source_gap_id)
    WHERE source_gap_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_pieces_source_strategy
    ON content_pieces (source_strategy_topic)
    WHERE source_strategy_topic IS NOT NULL;
