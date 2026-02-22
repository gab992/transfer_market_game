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


# ---------------------------------------------------------------------------
# Delete participant
# ---------------------------------------------------------------------------

def delete_participant(conn, participant_id: int) -> None:
    """
    Remove a participant from the game entirely.

    In order:
      1. Clears their roster (players return to the market automatically).
      2. Unlinks any user account tied to them (user can still log in, just
         without a team).
      3. Deletes the participant record itself.

    All steps run in a single transaction.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM participants WHERE id = %s FOR UPDATE",
            (participant_id,)
        )
        if not cur.fetchone():
            raise ValueError("Participant not found.")

        cur.execute("DELETE FROM rosters WHERE participant_id = %s", (participant_id,))
        cur.execute(
            "UPDATE users SET participant_id = NULL WHERE participant_id = %s",
            (participant_id,)
        )
        cur.execute("DELETE FROM participants WHERE id = %s", (participant_id,))

    conn.commit()


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

def create_milestone(conn, name: str, date: str,
                     show_total_value: bool,
                     show_value_change: bool,
                     show_pct_change: bool) -> dict:
    """
    Create a new milestone checkpoint.

    Args:
        name:              Display name for this milestone (e.g. "Week 4").
        date:              Target date string in ISO format (YYYY-MM-DD).
        show_total_value:  Whether to display each team's total value.
        show_value_change: Whether to display value change vs prior milestone.
        show_pct_change:   Whether to display % change vs prior milestone.

    Returns:
        The created milestone row.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO milestones
                (name, date, show_total_value, show_value_change, show_pct_change)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, date, show_total_value, show_value_change,
                      show_pct_change, snapshot_taken
        """, (name, date, show_total_value, show_value_change, show_pct_change))
        row = cur.fetchone()
    conn.commit()
    return row


def get_milestones(conn) -> list[dict]:
    """Return all milestones ordered by date ascending."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, date, show_total_value, show_value_change,
                   show_pct_change, snapshot_taken, created_at
            FROM milestones
            ORDER BY date ASC
        """)
        return cur.fetchall()


def get_upcoming_milestone(conn) -> dict | None:
    """
    Return the next milestone whose snapshot has not been taken yet,
    ordered by date. Returns None if there are no pending milestones.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, date
            FROM milestones
            WHERE snapshot_taken = FALSE
            ORDER BY date ASC
            LIMIT 1
        """)
        return cur.fetchone()


def capture_milestone_snapshot(conn, milestone_id: int) -> None:
    """
    Lock in the current team value for every participant and mark the
    milestone as snapshotted.

    Team value is the sum of current_value for all players on a participant's
    roster at the moment this function is called. Participants with no players
    get a team_value of 0.

    All inserts + the milestone update run in a single transaction.
    """
    with conn.cursor() as cur:
        # Verify milestone exists and hasn't already been snapshotted
        cur.execute(
            "SELECT id, snapshot_taken FROM milestones WHERE id = %s FOR UPDATE",
            (milestone_id,)
        )
        milestone = cur.fetchone()
        if not milestone:
            raise ValueError("Milestone not found.")
        if milestone["snapshot_taken"]:
            raise ValueError("Snapshot has already been taken for this milestone.")

        # Compute each participant's current team value and cash budget
        cur.execute("""
            SELECT
                p.id AS participant_id,
                p.budget,
                COALESCE(SUM(pl.current_value), 0) AS team_value
            FROM participants p
            LEFT JOIN rosters r  ON r.participant_id = p.id
            LEFT JOIN players pl ON pl.id = r.player_id
            GROUP BY p.id, p.budget
        """)
        snapshots = cur.fetchall()

        # Insert one row per participant (including their budget at this moment)
        for snap in snapshots:
            cur.execute("""
                INSERT INTO milestone_snapshots (milestone_id, participant_id, team_value, budget)
                VALUES (%s, %s, %s, %s)
            """, (milestone_id, snap["participant_id"], snap["team_value"], snap["budget"]))

        # Record every player's current value for per-player delta tracking
        cur.execute("SELECT id, current_value FROM players")
        for player in cur.fetchall():
            cur.execute("""
                INSERT INTO player_value_snapshots (milestone_id, player_id, value)
                VALUES (%s, %s, %s)
            """, (milestone_id, player["id"], player["current_value"]))

        # Mark snapshot as taken
        cur.execute(
            "UPDATE milestones SET snapshot_taken = TRUE WHERE id = %s",
            (milestone_id,)
        )

    conn.commit()


def get_milestone_results(conn, milestone_id: int) -> list[dict]:
    """
    Return per-participant results for a snapshotted milestone.

    Each row contains:
        participant_name : str
        team_value       : int   — value at this milestone
        prev_team_value  : int | None  — value at the prior milestone (if any)
        value_change     : int | None  — difference vs prior milestone
        pct_change       : float | None — % difference vs prior milestone

    Sorted by team_value descending.
    """
    with conn.cursor() as cur:
        # Fetch this milestone's snapshots
        cur.execute("""
            SELECT ms.participant_id, p.name AS participant_name, ms.team_value
            FROM milestone_snapshots ms
            JOIN participants p ON p.id = ms.participant_id
            WHERE ms.milestone_id = %s
            ORDER BY ms.team_value DESC
        """, (milestone_id,))
        current_snaps = cur.fetchall()

        # Find the previous milestone (latest snapshotted one before this one by date)
        cur.execute("""
            SELECT m.id
            FROM milestones m
            WHERE m.snapshot_taken = TRUE
              AND m.date < (SELECT date FROM milestones WHERE id = %s)
            ORDER BY m.date DESC
            LIMIT 1
        """, (milestone_id,))
        prev_row = cur.fetchone()

        # Fetch previous snapshots keyed by participant_id
        prev_by_participant: dict[int, int] = {}
        if prev_row:
            cur.execute("""
                SELECT participant_id, team_value
                FROM milestone_snapshots
                WHERE milestone_id = %s
            """, (prev_row["id"],))
            for row in cur.fetchall():
                prev_by_participant[row["participant_id"]] = row["team_value"]

    results = []
    for snap in current_snaps:
        prev_value = prev_by_participant.get(snap["participant_id"])
        if prev_value is not None:
            value_change = snap["team_value"] - prev_value
            pct_change = (value_change / prev_value * 100) if prev_value else None
        else:
            value_change = None
            pct_change = None

        results.append({
            "participant_name": snap["participant_name"],
            "team_value":       snap["team_value"],
            "prev_team_value":  prev_value,
            "value_change":     value_change,
            "pct_change":       pct_change,
        })

    return results


def get_last_milestone_player_values(conn) -> tuple[dict | None, dict]:
    """
    Return the per-player values from the most recently snapshotted milestone.

    Returns:
        A tuple of:
          - milestone info dict with keys (id, name, date), or None if no
            completed milestone exists.
          - a dict mapping player_id (int) -> value (int) at that milestone.
            Empty dict if no completed milestone exists.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, date
            FROM milestones
            WHERE snapshot_taken = TRUE
            ORDER BY date DESC
            LIMIT 1
        """)
        milestone = cur.fetchone()

        if not milestone:
            return None, {}

        cur.execute("""
            SELECT player_id, value
            FROM player_value_snapshots
            WHERE milestone_id = %s
        """, (milestone["id"],))
        values = {row["player_id"]: row["value"] for row in cur.fetchall()}

    return dict(milestone), values


def get_last_milestone_participant_snapshot(conn, participant_id: int) -> dict | None:
    """
    Return the most recent milestone snapshot for a specific participant.

    Returns a dict with keys:
        team_value     : int   — team value locked at that milestone
        budget         : int | None — cash balance at that milestone (None for
                         snapshots taken before the budget column was added)
        milestone_name : str
        milestone_date : date

    Returns None if no completed snapshot exists for this participant.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ms.team_value, ms.budget, m.name AS milestone_name, m.date AS milestone_date
            FROM milestone_snapshots ms
            JOIN milestones m ON m.id = ms.milestone_id
            WHERE ms.participant_id = %s
              AND m.snapshot_taken = TRUE
            ORDER BY m.date DESC
            LIMIT 1
        """, (participant_id,))
        row = cur.fetchone()

    return dict(row) if row else None


def delete_milestone(conn, milestone_id: int) -> None:
    """
    Delete a milestone and all its snapshots (via CASCADE).
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM milestones WHERE id = %s", (milestone_id,))
    conn.commit()


def clear_all_players(conn) -> int:
    """
    Remove every player from the database and clear all rosters.

    Players are removed from rosters first (so foreign-key constraints are
    satisfied), then the players themselves are deleted. Milestone snapshots
    and draft history are unaffected.

    Returns:
        The number of players deleted.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM players")
        count = cur.fetchone()["cnt"]

        cur.execute("DELETE FROM rosters")
        cur.execute("DELETE FROM players")

    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------

def get_active_draft(conn) -> dict | None:
    """Return the currently active draft, or None if no draft is in progress."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, num_rounds, snake, budget_bonus, status,
                   current_round, current_pick_idx, created_at
            FROM drafts
            WHERE status = 'active'
            LIMIT 1
        """)
        return cur.fetchone()


def get_draft_order(conn, draft_id: int) -> list[dict]:
    """
    Return the base pick order for a draft, ordered by position ascending.
    Each entry has: position (1-based), participant_id, participant_name.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT d.position, d.participant_id, p.name AS participant_name
            FROM draft_order d
            JOIN participants p ON p.id = d.participant_id
            WHERE d.draft_id = %s
            ORDER BY d.position ASC
        """, (draft_id,))
        return cur.fetchall()


def create_draft(conn, num_rounds: int, snake: bool, budget_bonus: int,
                 ordered_participant_ids: list[int]) -> dict:
    """
    Initiate a new draft.

    Steps (all in one transaction):
      1. Apply budget_bonus to every participant.
      2. Insert the draft record.
      3. Insert the draft_order rows.
      4. Advance past any initially-skippable participants.

    Args:
        num_rounds:               Total number of rounds.
        snake:                    Whether to reverse pick order on even rounds.
        budget_bonus:             Euros to add to every participant's budget.
        ordered_participant_ids:  Participant IDs in pick order (index 0 = picks 1st).

    Returns:
        The created draft row.
    """
    with conn.cursor() as cur:
        # 1. Apply budget bonus
        if budget_bonus > 0:
            cur.execute(
                "UPDATE participants SET budget = budget + %s",
                (budget_bonus,)
            )

        # 2. Insert draft
        cur.execute("""
            INSERT INTO drafts (num_rounds, snake, budget_bonus)
            VALUES (%s, %s, %s)
            RETURNING id, num_rounds, snake, budget_bonus, status,
                      current_round, current_pick_idx
        """, (num_rounds, snake, budget_bonus))
        draft = dict(cur.fetchone())
        draft_id = draft["id"]

        # 3. Insert pick order
        for i, pid in enumerate(ordered_participant_ids, start=1):
            cur.execute(
                "INSERT INTO draft_order (draft_id, position, participant_id) VALUES (%s, %s, %s)",
                (draft_id, i, pid)
            )

        # 4. Fetch base order and settle (advance past initially-skippable pickers)
        cur.execute("""
            SELECT position, participant_id
            FROM draft_order WHERE draft_id = %s ORDER BY position ASC
        """, (draft_id,))
        base_order = cur.fetchall()

        round_num, pick_idx, is_over = _settle_draft(
            conn, cur, base_order,
            start_round=1, start_idx=0,
            num_rounds=num_rounds, snake=snake,
            inclusive=True,   # check position 0 itself, not just after it
        )

        if is_over:
            cur.execute(
                "UPDATE drafts SET status = 'completed' WHERE id = %s",
                (draft_id,)
            )
        else:
            cur.execute(
                "UPDATE drafts SET current_round = %s, current_pick_idx = %s WHERE id = %s",
                (round_num, pick_idx, draft_id)
            )

    conn.commit()
    return draft


def advance_draft(conn, draft_id: int) -> None:
    """
    Move to the next valid pick after the current participant has made their
    selection (or was manually advanced).

    Automatically skips participants whose roster is full or budget is ≤ 0.
    Completes the draft if all rounds are exhausted or every participant is
    skipped consecutively.
    """
    with conn.cursor() as cur:
        # Lock the draft row for the duration of this transaction
        cur.execute(
            "SELECT id, num_rounds, snake, current_round, current_pick_idx "
            "FROM drafts WHERE id = %s FOR UPDATE",
            (draft_id,)
        )
        draft = cur.fetchone()
        if not draft or draft["status"] == "completed":
            return

        cur.execute("""
            SELECT position, participant_id
            FROM draft_order WHERE draft_id = %s ORDER BY position ASC
        """, (draft_id,))
        base_order = cur.fetchall()

        n = len(base_order)
        if n == 0:
            cur.execute("UPDATE drafts SET status = 'completed' WHERE id = %s", (draft_id,))
            conn.commit()
            return

        # Advance one step past the current pick
        next_idx = draft["current_pick_idx"] + 1
        next_round = draft["current_round"]
        if next_idx >= n:
            next_round += 1
            next_idx = 0

        round_num, pick_idx, is_over = _settle_draft(
            conn, cur, base_order,
            start_round=next_round, start_idx=next_idx,
            num_rounds=draft["num_rounds"], snake=draft["snake"],
            inclusive=True,
        )

        if is_over:
            cur.execute(
                "UPDATE drafts SET status = 'completed' WHERE id = %s",
                (draft_id,)
            )
        else:
            cur.execute(
                "UPDATE drafts SET current_round = %s, current_pick_idx = %s WHERE id = %s",
                (round_num, pick_idx, draft_id)
            )

    conn.commit()


def complete_draft(conn, draft_id: int) -> None:
    """Mark a draft as completed (used for early termination by the admin)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE drafts SET status = 'completed' WHERE id = %s",
            (draft_id,)
        )
    conn.commit()


def get_current_drafter_id(conn, draft_id: int) -> int | None:
    """
    Return the participant_id of whoever is currently up to pick,
    or None if the draft is completed.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, current_round, current_pick_idx, snake FROM drafts WHERE id = %s",
            (draft_id,)
        )
        draft = cur.fetchone()
        if not draft or draft["status"] == "completed":
            return None

        cur.execute("""
            SELECT position, participant_id
            FROM draft_order WHERE draft_id = %s ORDER BY position ASC
        """, (draft_id,))
        base_order = cur.fetchall()

    if not base_order:
        return None

    pick = _participant_at(base_order, draft["current_round"], draft["current_pick_idx"], draft["snake"])
    return pick["participant_id"]


# ---------------------------------------------------------------------------
# Draft private helpers
# ---------------------------------------------------------------------------

def _participant_at(base_order: list, round_num: int, pick_idx: int, snake: bool) -> dict:
    """
    Return the base_order entry for the given round and pick index.
    Snake drafts reverse the order on even-numbered rounds.
    """
    if snake and round_num % 2 == 0:
        return base_order[len(base_order) - 1 - pick_idx]
    return base_order[pick_idx]


def _should_skip(conn, cur, participant_id: int) -> bool:
    """
    Return True if this participant should be skipped during the draft:
    their roster is full or their budget is ≤ 0.
    """
    cur.execute(
        "SELECT budget FROM participants WHERE id = %s",
        (participant_id,)
    )
    p = cur.fetchone()
    if not p or p["budget"] <= 0:
        return True

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM rosters WHERE participant_id = %s",
        (participant_id,)
    )
    return cur.fetchone()["cnt"] >= MAX_ROSTER_SIZE


def _settle_draft(conn, cur, base_order: list,
                  start_round: int, start_idx: int,
                  num_rounds: int, snake: bool,
                  inclusive: bool = True) -> tuple[int, int, bool]:
    """
    Starting from (start_round, start_idx), find the next position that
    should NOT be skipped.

    Args:
        inclusive: If True, check (start_round, start_idx) itself first.
                   If False, advance one step before checking.

    Returns:
        (round_num, pick_idx, is_over)
        is_over is True if the draft should be ended.
    """
    n = len(base_order)
    if n == 0:
        return start_round, start_idx, True

    round_num = start_round
    pick_idx = start_idx
    consecutive_skips = 0

    if not inclusive:
        # Advance one step before starting to check
        pick_idx += 1
        if pick_idx >= n:
            round_num += 1
            pick_idx = 0

    while True:
        if round_num > num_rounds:
            return round_num, pick_idx, True

        pick = _participant_at(base_order, round_num, pick_idx, snake)
        if _should_skip(conn, cur, pick["participant_id"]):
            consecutive_skips += 1
            if consecutive_skips >= n:
                # Everyone has been skipped consecutively — draft is over
                return round_num, pick_idx, True
            # Advance one step
            pick_idx += 1
            if pick_idx >= n:
                round_num += 1
                pick_idx = 0
        else:
            # Found a valid picker
            return round_num, pick_idx, False
