-- Add duplicate-flagging support to players.
-- is_duplicate_flagged is set by the duplicate-check routine (db.run_duplicate_check).
-- When TRUE the player's name is highlighted in red everywhere it appears in the UI.
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS is_duplicate_flagged BOOLEAN NOT NULL DEFAULT FALSE;
