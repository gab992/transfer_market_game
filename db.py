from __future__ import annotations

"""
db.py — All database interactions for the Transfer Market Fantasy Game.

Every function takes an open psycopg2 connection as its first argument.
Business logic (budget checks, roster cap) lives here so the Streamlit app
stays simple.

Connection management is left to the caller (app.py uses st.cache_resource
to share a single connection across sessions).
"""

import psycopg2
import psycopg2.extras


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection(database_url: str):
    """
    Open and return a psycopg2 connection.
    Rows are returned as dicts (RealDictCursor) throughout this module.
    """
    return psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

def get_participants(conn) -> list[dict]:
    """Return all participants ordered by name."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, budget FROM participants ORDER BY name")
        return cur.fetchall()


def get_participant(conn, participant_id: int) -> dict | None:
    """Return a single participant by ID, or None if not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, budget FROM participants WHERE id = %s",
            (participant_id,)
        )
        return cur.fetchone()


def create_participant(conn, name: str, starting_budget: int = 100_000_000) -> dict:
    """
    Insert a new participant and return the created row.

    Args:
        name: The participant's display name (must be unique).
        starting_budget: Budget in euros. Defaults to €100M.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO participants (name, budget) VALUES (%s, %s) RETURNING id, name, budget",
            (name, starting_budget)
        )
        row = cur.fetchone()
    conn.commit()
    return row


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def get_all_players(conn) -> list[dict]:
    """Return every player in the database (owned or not)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, club, position, transfermrkt_url, current_value, last_updated "
            "FROM players ORDER BY name"
        )
        return cur.fetchall()


def get_available_players(conn) -> list[dict]:
    """
    Return players who are not currently on any roster (available to buy).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.name, p.club, p.position, p.current_value, p.last_updated
            FROM players p
            LEFT JOIN rosters r ON r.player_id = p.id
            WHERE r.player_id IS NULL
            ORDER BY p.current_value DESC
        """)
        return cur.fetchall()


def get_player_by_url(conn, url: str) -> dict | None:
    """Look up a player by their Transfermarkt URL. Returns None if not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, club, position, transfermrkt_url, current_value "
            "FROM players WHERE transfermrkt_url = %s",
            (url,)
        )
        return cur.fetchone()


def insert_player(conn, name: str, club: str, position: str,
                  transfermrkt_url: str, current_value: int) -> dict:
    """Insert a new player and return the created row."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO players (name, club, position, transfermrkt_url, current_value)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, club, position, transfermrkt_url, current_value
        """, (name, club, position, transfermrkt_url, current_value))
        row = cur.fetchone()
    conn.commit()
    return row


def update_player_value(conn, player_id: int, new_value: int) -> None:
    """Update a player's current_value and set last_updated to now."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE players
            SET current_value = %s, last_updated = NOW()
            WHERE id = %s
        """, (new_value, player_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Rosters
# ---------------------------------------------------------------------------

def get_roster(conn, participant_id: int) -> list[dict]:
    """
    Return the full roster for a participant, including each player's
    current value and what was paid for them.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.id,
                p.name,
                p.club,
                p.position,
                p.current_value,
                r.purchased_at_value,
                r.purchased_at
            FROM rosters r
            JOIN players p ON p.id = r.player_id
            WHERE r.participant_id = %s
            ORDER BY p.name
        """, (participant_id,))
        return cur.fetchall()


def get_roster_count(conn, participant_id: int) -> int:
    """Return the number of players currently on a participant's roster."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM rosters WHERE participant_id = %s",
            (participant_id,)
        )
        row = cur.fetchone()
        return row["cnt"]


# ---------------------------------------------------------------------------
# Buying and selling
# ---------------------------------------------------------------------------

MAX_ROSTER_SIZE = 15


def buy_existing_player(conn, participant_id: int, player_id: int) -> dict:
    """
    Purchase a player already in the database.

    Checks:
      - Participant has enough budget.
      - Participant has fewer than MAX_ROSTER_SIZE players.
      - Player is not already on another participant's roster.

    Deducts the player's current_value from the participant's budget,
    then inserts a row into rosters. All in one transaction.

    Returns:
        The updated participant row (with new budget).

    Raises:
        ValueError: if any business rule is violated.
    """
    with conn.cursor() as cur:
        # Lock both rows to prevent race conditions
        cur.execute(
            "SELECT id, name, budget FROM participants WHERE id = %s FOR UPDATE",
            (participant_id,)
        )
        participant = cur.fetchone()
        if not participant:
            raise ValueError("Participant not found.")

        cur.execute(
            "SELECT id, name, current_value FROM players WHERE id = %s FOR UPDATE",
            (player_id,)
        )
        player = cur.fetchone()
        if not player:
            raise ValueError("Player not found.")

        # Check the player is not already owned
        cur.execute(
            "SELECT participant_id FROM rosters WHERE player_id = %s",
            (player_id,)
        )
        if cur.fetchone():
            raise ValueError(f"{player['name']} is already on another team.")

        # Check roster cap
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM rosters WHERE participant_id = %s",
            (participant_id,)
        )
        if cur.fetchone()["cnt"] >= MAX_ROSTER_SIZE:
            raise ValueError(f"Roster is full ({MAX_ROSTER_SIZE} players max).")

        # Check budget
        price = player["current_value"]
        if participant["budget"] < price:
            raise ValueError(
                f"Insufficient budget. Need €{price:,}, have €{participant['budget']:,}."
            )

        # Deduct budget
        new_budget = participant["budget"] - price
        cur.execute(
            "UPDATE participants SET budget = %s WHERE id = %s",
            (new_budget, participant_id)
        )

        # Add to roster
        cur.execute(
            "INSERT INTO rosters (participant_id, player_id, purchased_at_value) VALUES (%s, %s, %s)",
            (participant_id, player_id, price)
        )

    conn.commit()
    return get_participant(conn, participant_id)


def buy_new_player(conn, participant_id: int, player_data: dict) -> dict:
    """
    Add a brand-new player (scraped from Transfermarkt) to the database
    and immediately purchase them for the given participant.

    If the player URL already exists in the DB (e.g. scraped before but
    not yet owned), this falls back to buy_existing_player.

    Args:
        participant_id: The buyer's ID.
        player_data: Dict returned by scraper.scrape_player(), containing:
                     name, club, position, transfermrkt_url, current_value.

    Returns:
        The updated participant row (with new budget).
    """
    # Check if this player is already in the DB (idempotent URL check)
    existing = get_player_by_url(conn, player_data["transfermrkt_url"])
    if existing:
        return buy_existing_player(conn, participant_id, existing["id"])

    # Otherwise insert the new player first, then buy them — all in one transaction
    with conn.cursor() as cur:
        # Lock participant row
        cur.execute(
            "SELECT id, name, budget FROM participants WHERE id = %s FOR UPDATE",
            (participant_id,)
        )
        participant = cur.fetchone()
        if not participant:
            raise ValueError("Participant not found.")

        # Check roster cap
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM rosters WHERE participant_id = %s",
            (participant_id,)
        )
        if cur.fetchone()["cnt"] >= MAX_ROSTER_SIZE:
            raise ValueError(f"Roster is full ({MAX_ROSTER_SIZE} players max).")

        price = player_data["current_value"]
        if participant["budget"] < price:
            raise ValueError(
                f"Insufficient budget. Need €{price:,}, have €{participant['budget']:,}."
            )

        # Insert player
        cur.execute("""
            INSERT INTO players (name, club, position, transfermrkt_url, current_value)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            player_data["name"],
            player_data["club"],
            player_data["position"],
            player_data["transfermrkt_url"],
            price,
        ))
        player_id = cur.fetchone()["id"]

        # Deduct budget
        cur.execute(
            "UPDATE participants SET budget = %s WHERE id = %s",
            (participant["budget"] - price, participant_id)
        )

        # Add to roster
        cur.execute(
            "INSERT INTO rosters (participant_id, player_id, purchased_at_value) VALUES (%s, %s, %s)",
            (participant_id, player_id, price)
        )

    conn.commit()
    return get_participant(conn, participant_id)


def sell_player(conn, participant_id: int, player_id: int) -> dict:
    """
    Sell a player from a participant's roster.

    Adds the player's *current* market value (not what was paid) to the
    participant's budget, then removes the roster row. The player record
    stays in the `players` table and becomes available for others to buy.

    Returns:
        The updated participant row (with new budget).

    Raises:
        ValueError: if the player is not on this participant's roster.
    """
    with conn.cursor() as cur:
        # Verify ownership and get the player's current value
        cur.execute("""
            SELECT p.current_value, p.name
            FROM rosters r
            JOIN players p ON p.id = r.player_id
            WHERE r.participant_id = %s AND r.player_id = %s
        """, (participant_id, player_id))
        row = cur.fetchone()
        if not row:
            raise ValueError("This player is not on your roster.")

        sale_price = row["current_value"]

        # Add sale price to budget
        cur.execute(
            "UPDATE participants SET budget = budget + %s WHERE id = %s",
            (sale_price, participant_id)
        )

        # Remove from roster (player stays in `players` table)
        cur.execute(
            "DELETE FROM rosters WHERE participant_id = %s AND player_id = %s",
            (participant_id, player_id)
        )

    conn.commit()
    return get_participant(conn, participant_id)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def get_leaderboard(conn) -> list[dict]:
    """
    Return all participants ranked by total team value (sum of current
    market values of all players on their roster).

    Columns: rank, name, budget, team_value, total_assets, roster_count
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.id,
                p.name,
                p.budget,
                COALESCE(SUM(pl.current_value), 0)  AS team_value,
                p.budget + COALESCE(SUM(pl.current_value), 0) AS total_assets,
                COUNT(r.player_id)                  AS roster_count
            FROM participants p
            LEFT JOIN rosters r  ON r.participant_id = p.id
            LEFT JOIN players pl ON pl.id = r.player_id
            GROUP BY p.id, p.name, p.budget
            ORDER BY total_assets DESC
        """)
        rows = cur.fetchall()

    # Add rank numbers
    return [dict(row, rank=i + 1) for i, row in enumerate(rows)]
