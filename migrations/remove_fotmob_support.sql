-- Remove FotMob scraping support
-- Drops the fotmob_* columns, transfermrkt_value, and the game_settings table
-- that were added in migrations/add_fotmob_support.sql.
--
-- To apply:
--   docker exec -i transfer_market_game-db-1 psql -U postgres -d transfer_market_game < migrations/remove_fotmob_support.sql

ALTER TABLE players DROP COLUMN IF EXISTS fotmob_url;
ALTER TABLE players DROP COLUMN IF EXISTS fotmob_value;
ALTER TABLE players DROP COLUMN IF EXISTS fotmob_last_updated;
ALTER TABLE players DROP COLUMN IF EXISTS transfermrkt_value;

DROP TABLE IF EXISTS game_settings;
