-- Migration: add transfers feed table
-- Logs every buy and sell so all participants can see each other's moves.

CREATE TABLE IF NOT EXISTS transfers (
    id               SERIAL PRIMARY KEY,
    participant_id   INT REFERENCES participants(id) ON DELETE SET NULL,
    participant_name TEXT NOT NULL,
    player_id        INT REFERENCES players(id) ON DELETE SET NULL,
    player_name      TEXT NOT NULL,
    player_club      TEXT NOT NULL,
    player_position  TEXT NOT NULL,
    transfer_type    TEXT NOT NULL CHECK (transfer_type IN ('buy', 'sell')),
    value            BIGINT NOT NULL,
    transferred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS transfers_transferred_at_idx ON transfers (transferred_at DESC);
