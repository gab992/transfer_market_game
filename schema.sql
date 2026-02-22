-- Transfer Market Fantasy Game — Database Schema
-- Run once to set up the database:
--   psql your_database_name < schema.sql

-- Each person participating in the game.
-- Budget is stored in euros (integer, e.g. 100000000 = €100M).
-- Starting budget is set when the row is inserted.
CREATE TABLE participants (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    budget  BIGINT NOT NULL DEFAULT 100000000  -- €100M starting budget
);

-- Every soccer player that has been added to the game via a Transfermarkt URL.
-- Players persist in this table even after being sold; they become available
-- for other participants to buy from the market.
CREATE TABLE players (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    club                TEXT,
    position            TEXT,
    transfermrkt_url    TEXT NOT NULL UNIQUE,  -- used to identify and re-scrape the player
    current_value       BIGINT NOT NULL,        -- market value in euros
    last_updated        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- App login accounts. Each user can optionally be linked to one participant
-- (their team). The admin account typically has participant_id = NULL since
-- the admin may not be playing. Only the admin can create user accounts.
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    participant_id  INT UNIQUE REFERENCES participants(id),  -- one-to-one, nullable
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Admin-defined checkpoints for evaluating game performance.
-- The admin chooses which metrics to display and manually triggers a snapshot
-- to lock in team values when ready.
CREATE TABLE milestones (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    date                DATE NOT NULL,
    show_total_value    BOOLEAN NOT NULL DEFAULT TRUE,
    show_value_change   BOOLEAN NOT NULL DEFAULT FALSE,  -- change vs prior milestone
    show_pct_change     BOOLEAN NOT NULL DEFAULT FALSE,  -- % change vs prior milestone
    snapshot_taken      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Locks in each participant's team value and cash budget when the admin takes
-- a snapshot. Cascades on milestone delete so snapshots are removed with their
-- milestone.
CREATE TABLE milestone_snapshots (
    milestone_id    INT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    participant_id  INT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    team_value      BIGINT NOT NULL,
    budget          BIGINT,             -- participant's cash balance at snapshot time
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (milestone_id, participant_id)
);

-- Records every player's market value at the moment a milestone snapshot is
-- taken. Enables "change since last milestone" on individual player cards.
-- Cascades on milestone or player delete.
CREATE TABLE player_value_snapshots (
    milestone_id    INT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    player_id       INT NOT NULL REFERENCES players(id)    ON DELETE CASCADE,
    value           BIGINT NOT NULL,
    PRIMARY KEY (milestone_id, player_id)
);

-- Admin-initiated draft sessions. While a draft is 'active' the free market
-- is locked and participants take turns buying one player per round.
-- After the draft completes (or is ended early) status flips to 'completed'
-- and normal buying resumes.
CREATE TABLE drafts (
    id               SERIAL PRIMARY KEY,
    num_rounds       INT NOT NULL,
    snake            BOOLEAN NOT NULL DEFAULT FALSE,    -- reverse order each round
    budget_bonus     BIGINT NOT NULL DEFAULT 0,         -- added to all budgets at draft start
    status           TEXT NOT NULL DEFAULT 'active',    -- 'active' | 'completed'
    current_round    INT NOT NULL DEFAULT 1,
    current_pick_idx INT NOT NULL DEFAULT 0,            -- 0-based index into current round order
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Base pick order for a draft. Snake reversal is computed at query time.
CREATE TABLE draft_order (
    draft_id       INT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    position       INT NOT NULL,   -- 1-based
    participant_id INT NOT NULL REFERENCES participants(id),
    PRIMARY KEY (draft_id, position)
);

-- Links participants to the players currently on their roster.
-- A row here means the participant owns that player right now.
-- When a player is sold, the row is deleted (player stays in `players`).
CREATE TABLE rosters (
    participant_id      INT NOT NULL REFERENCES participants(id),
    player_id           INT NOT NULL REFERENCES players(id),
    purchased_at_value  BIGINT NOT NULL,        -- value at time of purchase, for reference
    purchased_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (participant_id, player_id)     -- a player can only be on one roster at a time
);
