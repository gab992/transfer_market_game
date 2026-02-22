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
