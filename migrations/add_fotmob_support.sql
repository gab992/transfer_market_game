-- Add FotMob scraping support
-- Adds fotmob_url, fotmob_value, fotmob_last_updated columns to players table.
-- Also adds transfermrkt_value to store TM-scraped values independently.
-- Introduces a game_settings table for game-wide configuration (starting with value_source).
--
-- To apply:
--   docker exec -i transfer_market_game-db-1 psql -U postgres -d transfer_market_game < migrations/add_fotmob_support.sql

ALTER TABLE players ADD COLUMN IF NOT EXISTS fotmob_url          TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS fotmob_value        BIGINT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS fotmob_last_updated TIMESTAMPTZ;
ALTER TABLE players ADD COLUMN IF NOT EXISTS transfermrkt_value  BIGINT;

-- Backfill transfermrkt_value from current_value (existing data is from TM)
UPDATE players SET transfermrkt_value = current_value WHERE transfermrkt_value IS NULL;

CREATE TABLE IF NOT EXISTS game_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO game_settings (key, value)
VALUES ('value_source', 'transfermrkt')
ON CONFLICT DO NOTHING;
