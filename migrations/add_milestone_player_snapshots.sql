-- Migration: per-player value snapshots + budget tracking at milestones
--
-- Adds two things:
--   1. player_value_snapshots — records every player's market value when a
--      milestone snapshot is taken. Enables "change since last milestone" on
--      player cards.
--   2. budget column on milestone_snapshots — records each participant's cash
--      balance at snapshot time. Enables budget-change deltas on My Team.
--
-- Apply with:
--   psql $DATABASE_URL < migrations/add_milestone_player_snapshots.sql

CREATE TABLE IF NOT EXISTS player_value_snapshots (
    milestone_id    INT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    player_id       INT NOT NULL REFERENCES players(id)    ON DELETE CASCADE,
    value           BIGINT NOT NULL,
    PRIMARY KEY (milestone_id, player_id)
);

ALTER TABLE milestone_snapshots ADD COLUMN IF NOT EXISTS budget BIGINT;
