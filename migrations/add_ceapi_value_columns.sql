-- Add ceapi-specific market value columns to players.
-- The ceapi backend fetches real-time values from Transfermarkt's internal
-- JSON endpoint, which bypasses Cloudflare bot detection.
-- current_value is always updated to reflect the latest refresh regardless
-- of which source was used.

ALTER TABLE players
    ADD COLUMN IF NOT EXISTS ceapi_value           BIGINT,
    ADD COLUMN IF NOT EXISTS ceapi_last_updated    TIMESTAMPTZ;
