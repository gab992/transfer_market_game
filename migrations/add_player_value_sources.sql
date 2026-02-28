-- Add source-specific market value columns to players.
-- This lets both data acquisition backends (Kaggle dataset and Transfermarkt
-- live scrape) store their values independently, so switching between them
-- is non-destructive. current_value always reflects the most recently refreshed
-- source and is what the game uses for budget calculations.

ALTER TABLE players
    ADD COLUMN IF NOT EXISTS kaggle_value           BIGINT,
    ADD COLUMN IF NOT EXISTS kaggle_last_updated    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS transfermarkt_value    BIGINT,
    ADD COLUMN IF NOT EXISTS transfermarkt_last_updated TIMESTAMPTZ;
