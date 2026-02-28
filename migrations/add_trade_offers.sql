-- Migration: add direct trade offer system
-- Run with: psql $DATABASE_URL < migrations/add_trade_offers.sql

-- Trade offers sent between participants. Each offer specifies money and/or
-- players flowing in each direction. Status transitions:
--   pending -> accepted (receiver accepts)
--   pending -> declined (receiver declines)
--   pending -> cancelled (sender cancels)
CREATE TABLE trade_offers (
    id              SERIAL PRIMARY KEY,
    sender_id       INTEGER NOT NULL REFERENCES participants(id),
    receiver_id     INTEGER NOT NULL REFERENCES participants(id),
    sender_name     TEXT NOT NULL,
    receiver_name   TEXT NOT NULL,
    sender_money    BIGINT NOT NULL DEFAULT 0,    -- money the sender gives to the receiver
    receiver_money  BIGINT NOT NULL DEFAULT 0,    -- money the receiver gives to the sender
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | declined | cancelled
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Individual players attached to a trade offer. Names and club/position are
-- denormalised so the offer display still makes sense after players change hands.
CREATE TABLE trade_offer_players (
    offer_id        INTEGER NOT NULL REFERENCES trade_offers(id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(id),
    player_name     TEXT NOT NULL,
    player_club     TEXT,
    player_position TEXT,
    -- 'sender_gives'   = sender is offering this player to the receiver
    -- 'receiver_gives' = receiver is being asked to give this player to the sender
    direction       TEXT NOT NULL CHECK (direction IN ('sender_gives', 'receiver_gives')),
    PRIMARY KEY (offer_id, player_id)
);
