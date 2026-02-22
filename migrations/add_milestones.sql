-- Migration: add milestones and milestone_snapshots tables
-- Run against an existing database:
--   docker exec -i transfer_market_game-db-1 psql -U postgres -d transfer_market_game < migrations/add_milestones.sql

CREATE TABLE milestones (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    date                DATE NOT NULL,
    show_total_value    BOOLEAN NOT NULL DEFAULT TRUE,
    show_value_change   BOOLEAN NOT NULL DEFAULT FALSE,
    show_pct_change     BOOLEAN NOT NULL DEFAULT FALSE,
    snapshot_taken      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE milestone_snapshots (
    milestone_id    INT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    participant_id  INT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    team_value      BIGINT NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (milestone_id, participant_id)
);
