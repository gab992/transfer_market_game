-- Migration: add users table
-- Run against an existing database that was set up before auth was added:
--   psql transfer_market_game < migrations/add_users.sql

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    participant_id  INT UNIQUE REFERENCES participants(id),  -- one-to-one, nullable
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
