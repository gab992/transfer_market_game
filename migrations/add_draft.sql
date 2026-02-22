-- Migration: add draft tables
-- Run against an existing database:
--   docker exec -i transfer_market_game-db-1 psql -U postgres -d transfer_market_game < migrations/add_draft.sql

CREATE TABLE drafts (
    id               SERIAL PRIMARY KEY,
    num_rounds       INT NOT NULL,
    snake            BOOLEAN NOT NULL DEFAULT FALSE,
    budget_bonus     BIGINT NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'active',
    current_round    INT NOT NULL DEFAULT 1,
    current_pick_idx INT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE draft_order (
    draft_id       INT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    position       INT NOT NULL,
    participant_id INT NOT NULL REFERENCES participants(id),
    PRIMARY KEY (draft_id, position)
);
