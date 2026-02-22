"""
app.py — Streamlit UI for the Transfer Market Fantasy Game.

Run with:
    uv run streamlit run app.py

Five pages (Admin is only visible to admin users):
  1. Leaderboard  — standings ranked by total assets (visible to all)
  2. My Team      — view and manage your own roster
  3. Market       — buy available players or add a new one by Transfermarkt URL
  4. Milestones   — upcoming milestone date and historical milestone results
  5. Admin        — manage participants, users, and milestones (admin only)
"""

import os
import streamlit as st
from dotenv import load_dotenv

import db
import auth
import scraper

load_dotenv()

st.set_page_config(page_title="Transfer Market Game", page_icon="⚽", layout="wide")


# ---------------------------------------------------------------------------
# Shared database connection (one connection for the whole app session)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_conn():
    # st.secrets is populated from the Streamlit Community Cloud dashboard in
    # production, and from .streamlit/secrets.toml locally (if present).
    # Falls back to the .env file for local development via load_dotenv() above.
    # st.secrets raises an error (not just returns None) when no secrets file
    # exists at all, so we use a try/except rather than .get()
    try:
        url = st.secrets["DATABASE_URL"]
    except Exception:
        url = os.environ["DATABASE_URL"]
    return db.get_connection(url)


conn = get_conn()


# ---------------------------------------------------------------------------
# Auth gate — nothing below renders until the user is logged in
# ---------------------------------------------------------------------------

st.title("⚽ Transfer Market Fantasy Game")
auth.require_login(conn)

# From here on, auth.current_user() is guaranteed to be set.
user = auth.current_user()


# ---------------------------------------------------------------------------
# Sidebar — navigation + user info
# ---------------------------------------------------------------------------

with st.sidebar:
    st.write(f"Logged in as **{user['username']}**")

    # Show a draft-active indicator so all users are aware
    _sidebar_draft = db.get_active_draft(conn)
    if _sidebar_draft:
        st.warning(f"🟡 DRAFT ACTIVE — Round {_sidebar_draft['current_round']} of {_sidebar_draft['num_rounds']}")

    if st.button("Log out", use_container_width=True):
        del st.session_state["user"]
        st.rerun()

    st.divider()

    pages = ["Leaderboard", "My Team", "Market", "Milestones"]
    if auth.is_admin():
        pages.append("Admin")

    page = st.radio("Navigate", pages)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_euros(value: int) -> str:
    """Format an integer number of euros as a readable string, e.g. €45.5M."""
    if value >= 1_000_000:
        return f"€{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"€{value / 1_000:.0f}K"
    return f"€{value:,}"


# ---------------------------------------------------------------------------
# Page: Leaderboard
# ---------------------------------------------------------------------------

def page_leaderboard():
    st.header("Leaderboard")
    st.caption("Ranked by total assets (budget remaining + current team value).")

    rows = db.get_leaderboard(conn)

    if not rows:
        st.info("No participants yet. An admin can add them in the Admin page.")
        return

    data = []
    for row in rows:
        data.append({
            "Rank":         row["rank"],
            "Participant":  row["name"],
            "Budget Left":  fmt_euros(row["budget"]),
            "Team Value":   fmt_euros(row["team_value"]),
            "Total Assets": fmt_euros(row["total_assets"]),
            "Players":      f"{row['roster_count']} / {db.MAX_ROSTER_SIZE}",
        })

    st.dataframe(data, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Team Rosters")

    for row in rows:
        roster = db.get_roster(conn, row["id"])
        label = f"**{row['name']}** — {len(roster)} player{'s' if len(roster) != 1 else ''}"
        with st.expander(label):
            if roster:
                for player in roster:
                    value_change = player["current_value"] - player["purchased_at_value"]
                    change_str = (
                        f" (+{fmt_euros(value_change)})" if value_change > 0
                        else f" ({fmt_euros(value_change)})" if value_change < 0
                        else ""
                    )
                    st.markdown(
                        f"**{player['name']}** — {player['club']} · {player['position']}  \n"
                        f"Value: **{fmt_euros(player['current_value'])}**{change_str} · "
                        f"Paid: {fmt_euros(player['purchased_at_value'])}"
                    )
            else:
                st.write("No players yet.")


# ---------------------------------------------------------------------------
# Page: My Team
# ---------------------------------------------------------------------------

def page_my_team():
    st.header("My Team")

    participant_id = user["participant_id"]

    if not participant_id:
        st.info("Your account is not linked to a participant. Ask the admin to link your account to your team.")
        return

    participant = db.get_participant(conn, participant_id)
    roster = db.get_roster(conn, participant_id)

    team_value = sum(p["current_value"] for p in roster)
    col1, col2, col3 = st.columns(3)
    col1.metric("Budget Remaining", fmt_euros(participant["budget"]))
    col2.metric("Team Value",       fmt_euros(team_value))
    col3.metric("Players",          f"{len(roster)} / {db.MAX_ROSTER_SIZE}")

    st.divider()

    if not roster:
        st.info("Your roster is empty. Head to the Market to buy players.")
        return

    st.subheader("Roster")

    for player in roster:
        col_info, col_btn = st.columns([4, 1])
        with col_info:
            value_change = player["current_value"] - player["purchased_at_value"]
            change_str = (
                f"  (+{fmt_euros(value_change)})" if value_change > 0
                else f"  ({fmt_euros(value_change)})"   if value_change < 0
                else ""
            )
            st.markdown(
                f"**{player['name']}** — {player['club']} · {player['position']}  \n"
                f"Current value: **{fmt_euros(player['current_value'])}**{change_str}  \n"
                f"Purchased at: {fmt_euros(player['purchased_at_value'])}"
            )
        with col_btn:
            if st.button("Sell", key=f"sell_{player['id']}"):
                try:
                    updated = db.sell_player(conn, participant_id, player["id"])
                    st.success(
                        f"Sold {player['name']} for {fmt_euros(player['current_value'])}. "
                        f"New budget: {fmt_euros(updated['budget'])}"
                    )
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ---------------------------------------------------------------------------
# Page: Market
# ---------------------------------------------------------------------------

def page_market():
    st.header("Market")

    participant_id = user["participant_id"]

    if not participant_id:
        st.info("Your account is not linked to a participant. Ask the admin to link your account to your team.")
        return

    participant = db.get_participant(conn, participant_id)
    st.caption(f"Budget remaining: **{fmt_euros(participant['budget'])}**")

    # --- Draft status panel ---
    active_draft = db.get_active_draft(conn)
    my_turn = False

    if active_draft:
        base_order = db.get_draft_order(conn, active_draft["id"])
        current_drafter_id = db.get_current_drafter_id(conn, active_draft["id"])
        my_turn = (current_drafter_id == participant_id)

        # Build the display order for the current round (accounting for snake)
        n = len(base_order)
        if active_draft["snake"] and active_draft["current_round"] % 2 == 0:
            round_order = list(reversed(base_order))
        else:
            round_order = list(base_order)

        current_drafter_name = next(
            (p["participant_name"] for p in base_order if p["participant_id"] == current_drafter_id),
            "Unknown"
        )

        st.info(
            f"**Draft active — Round {active_draft['current_round']} of {active_draft['num_rounds']}**  \n"
            f"Currently picking: **{current_drafter_name}**  \n"
            f"Pick order this round: "
            + " → ".join(
                f"**{p['participant_name']}**" if p["participant_id"] == current_drafter_id
                else p["participant_name"]
                for p in round_order
            )
        )

        if not my_turn:
            st.warning(f"It's not your turn. Waiting for **{current_drafter_name}** to pick.")

    st.divider()

    # --- Section 1: Buy an available player (already in the DB) ---
    st.subheader("Available Players")
    available = db.get_available_players(conn)

    # During a draft, only the current drafter can buy
    buying_allowed = not active_draft or my_turn

    if available:
        for player in available:
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                st.markdown(
                    f"**{player['name']}** — {player['club']} · {player['position']}  \n"
                    f"Value: **{fmt_euros(player['current_value'])}**"
                )
            with col_btn:
                affordable = player["current_value"] <= participant["budget"]
                can_buy = buying_allowed and affordable
                if st.button(
                    "Buy",
                    key=f"buy_{player['id']}",
                    disabled=not can_buy,
                    help=(
                        "Not your turn" if not buying_allowed
                        else None if affordable
                        else "Insufficient budget"
                    ),
                ):
                    try:
                        updated = db.buy_existing_player(conn, participant_id, player["id"])
                        if active_draft:
                            db.advance_draft(conn, active_draft["id"])
                        st.success(
                            f"Bought {player['name']} for {fmt_euros(player['current_value'])}. "
                            f"New budget: {fmt_euros(updated['budget'])}"
                        )
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
    else:
        st.info("No players currently available. Add a new player below.")

    # During a draft, hide the "add new by URL" section if it's not your turn —
    # looking up and adding a new player should be reserved for the active drafter.
    if active_draft and not my_turn:
        return

    st.divider()

    # --- Section 2: Add a new player by Transfermarkt URL ---
    st.subheader("Add a New Player by URL")
    st.caption(
        "Paste a Transfermarkt player profile URL to fetch their details and buy them. "
        "Example: https://www.transfermarkt.com/erling-haaland/profil/spieler/418560"
    )

    url_input = st.text_input("Transfermarkt URL", placeholder="https://www.transfermarkt.com/...")

    if url_input:
        if st.button("Look up player"):
            with st.spinner("Fetching player data from Transfermarkt..."):
                try:
                    data = scraper.scrape_player(url_input)
                    st.session_state["pending_player"] = data
                except Exception as e:
                    st.error(f"Could not fetch player: {e}")
                    st.session_state.pop("pending_player", None)

    # Confirmation step — shown after a successful lookup
    if "pending_player" in st.session_state:
        data = st.session_state["pending_player"]
        st.markdown(
            f"**{data['name']}** — {data['club']} · {data['position']}  \n"
            f"Current value: **{fmt_euros(data['current_value'])}**"
        )

        affordable = data["current_value"] <= participant["budget"]
        if not affordable:
            st.warning(
                f"You can't afford this player. "
                f"Need {fmt_euros(data['current_value'])}, have {fmt_euros(participant['budget'])}."
            )

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button("Confirm Purchase", disabled=not affordable):
                try:
                    updated = db.buy_new_player(conn, participant_id, data)
                    if active_draft:
                        db.advance_draft(conn, active_draft["id"])
                    st.success(
                        f"Bought {data['name']} for {fmt_euros(data['current_value'])}. "
                        f"New budget: {fmt_euros(updated['budget'])}"
                    )
                    st.session_state.pop("pending_player", None)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        with col_cancel:
            if st.button("Cancel"):
                st.session_state.pop("pending_player", None)
                st.rerun()


# ---------------------------------------------------------------------------
# Page: Milestones (visible to all users)
# ---------------------------------------------------------------------------

def page_milestones():
    st.header("Milestones")

    # --- Upcoming milestone banner ---
    upcoming = db.get_upcoming_milestone(conn)
    if upcoming:
        st.info(f"Next milestone: **{upcoming['name']}** on {upcoming['date'].strftime('%d %B %Y')}")
    else:
        st.caption("No upcoming milestones scheduled.")

    st.divider()

    # --- Past milestone results ---
    milestones = db.get_milestones(conn)
    completed = [m for m in milestones if m["snapshot_taken"]]

    if not completed:
        st.info("No milestone snapshots have been taken yet.")
        return

    for milestone in reversed(completed):  # Most recent first
        st.subheader(f"{milestone['name']} — {milestone['date'].strftime('%d %B %Y')}")
        results = db.get_milestone_results(conn, milestone["id"])

        if not results:
            st.write("No snapshot data available.")
            continue

        # Determine which columns to show based on admin's metric choices
        # and whether prior-milestone data actually exists
        has_prior_data = any(r["prev_team_value"] is not None for r in results)

        table_rows = []
        for i, r in enumerate(results):
            row = {"Rank": i + 1, "Participant": r["participant_name"]}

            if milestone["show_total_value"]:
                row["Team Value"] = fmt_euros(r["team_value"])

            if milestone["show_value_change"] and has_prior_data:
                if r["value_change"] is not None:
                    sign = "+" if r["value_change"] >= 0 else ""
                    row["Value Change"] = f"{sign}{fmt_euros(r['value_change'])}"
                else:
                    row["Value Change"] = "—"

            if milestone["show_pct_change"] and has_prior_data:
                if r["pct_change"] is not None:
                    sign = "+" if r["pct_change"] >= 0 else ""
                    row["% Change"] = f"{sign}{r['pct_change']:.1f}%"
                else:
                    row["% Change"] = "—"

            table_rows.append(row)

        st.dataframe(table_rows, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page: Admin (admin users only)
# ---------------------------------------------------------------------------

def page_admin():
    if not auth.is_admin():
        st.error("You do not have permission to view this page.")
        return

    st.header("Admin")

    # ---- Section: Participants ----
    st.subheader("Participants")
    st.caption("Add or remove game participants.")

    with st.form("add_participant"):
        name = st.text_input("Participant name")
        budget = st.number_input(
            "Starting budget (€)",
            min_value=1_000_000,
            max_value=1_000_000_000,
            value=100_000_000,
            step=5_000_000,
        )
        if st.form_submit_button("Add Participant"):
            if not name.strip():
                st.error("Name cannot be empty.")
            else:
                try:
                    db.create_participant(conn, name.strip(), int(budget))
                    st.success(f"Added participant '{name}' with budget {fmt_euros(int(budget))}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not add participant: {e}")

    participants = db.get_participants(conn)
    if participants:
        for p in participants:
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                st.write(f"**{p['name']}** — budget: {fmt_euros(p['budget'])}")
            with col_btn:
                if st.button("Delete", key=f"del_participant_{p['id']}"):
                    # Store ID for confirmation step
                    st.session_state["confirm_delete_participant"] = p["id"]

        # Confirmation dialog for participant deletion
        if "confirm_delete_participant" in st.session_state:
            pid = st.session_state["confirm_delete_participant"]
            target = next((p for p in participants if p["id"] == pid), None)
            if target:
                st.warning(
                    f"Delete **{target['name']}**? This will clear their roster and "
                    f"unlink their user account. This cannot be undone."
                )
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("Yes, delete", type="primary"):
                        try:
                            db.delete_participant(conn, pid)
                            st.success(f"Deleted participant '{target['name']}'.")
                        except Exception as e:
                            st.error(str(e))
                        del st.session_state["confirm_delete_participant"]
                        st.rerun()
                with col_no:
                    if st.button("Cancel"):
                        del st.session_state["confirm_delete_participant"]
                        st.rerun()
    else:
        st.info("No participants yet.")

    st.divider()

    # ---- Section: User Accounts ----
    st.subheader("User Accounts")
    st.caption("Create login accounts and link them to participants.")

    # Map participant name -> id for the dropdown (only unlinked participants)
    all_users = auth.get_all_users(conn)
    linked_participant_ids = {u["participant_id"] for u in all_users if u["participant_id"]}
    unlinked_participants = [p for p in participants if p["id"] not in linked_participant_ids]
    participant_options = {"(None — admin account)": None} | {
        p["name"]: p["id"] for p in unlinked_participants
    }

    with st.form("add_user"):
        new_username = st.text_input("Username")
        new_password = st.text_input("Password", type="password")
        new_is_admin = st.checkbox("Admin privileges")
        linked_name  = st.selectbox("Link to participant", options=list(participant_options.keys()))

        if st.form_submit_button("Create User"):
            if not new_username.strip() or not new_password:
                st.error("Username and password are required.")
            else:
                try:
                    auth.create_user(
                        conn,
                        username=new_username.strip(),
                        password=new_password,
                        is_admin=new_is_admin,
                        participant_id=participant_options[linked_name],
                    )
                    st.success(f"User '{new_username}' created.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create user: {e}")

    # List existing users
    if all_users:
        st.write("")
        for u in all_users:
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                role   = "Admin" if u["is_admin"] else "Basic"
                linked = f"→ {u['participant_name']}" if u["participant_name"] else "(no team)"
                st.write(f"**{u['username']}** · {role} · {linked}")
            with col_btn:
                # Prevent the admin from deleting their own account
                if u["id"] != user["id"]:
                    if st.button("Delete", key=f"del_user_{u['id']}"):
                        auth.delete_user(conn, u["id"])
                        st.rerun()
    else:
        st.info("No user accounts yet.")

    st.divider()

    # ---- Section: Milestones ----
    st.subheader("Milestones")
    st.caption("Define checkpoints to evaluate team performance. Take a snapshot when ready to lock in values.")

    all_milestones = db.get_milestones(conn)

    # Only allow "since last milestone" metrics if at least one snapshot exists
    has_any_snapshot = any(m["snapshot_taken"] for m in all_milestones)

    with st.form("add_milestone"):
        m_name  = st.text_input("Milestone name", placeholder="e.g. Week 4")
        m_date  = st.date_input("Date")
        st.write("Metrics to display:")
        m_total  = st.checkbox("Total team value", value=True)
        m_change = st.checkbox(
            "Value change since last milestone",
            disabled=not has_any_snapshot,
            help="Only available after the first milestone snapshot is taken." if not has_any_snapshot else None,
        )
        m_pct    = st.checkbox(
            "% change since last milestone",
            disabled=not has_any_snapshot,
            help="Only available after the first milestone snapshot is taken." if not has_any_snapshot else None,
        )

        if st.form_submit_button("Create Milestone"):
            if not m_name.strip():
                st.error("Milestone name cannot be empty.")
            else:
                try:
                    db.create_milestone(
                        conn,
                        name=m_name.strip(),
                        date=m_date.isoformat(),
                        show_total_value=m_total,
                        show_value_change=m_change,
                        show_pct_change=m_pct,
                    )
                    st.success(f"Milestone '{m_name}' created.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create milestone: {e}")

    # List all milestones
    if all_milestones:
        st.write("")
        for m in all_milestones:
            col_info, col_snap, col_del = st.columns([4, 1, 1])
            with col_info:
                status = "✓ Snapshot taken" if m["snapshot_taken"] else "Pending snapshot"
                metrics = []
                if m["show_total_value"]:  metrics.append("Total value")
                if m["show_value_change"]: metrics.append("Value change")
                if m["show_pct_change"]:   metrics.append("% change")
                st.write(
                    f"**{m['name']}** — {m['date'].strftime('%d %b %Y')}  \n"
                    f"{status} · Metrics: {', '.join(metrics)}"
                )
            with col_snap:
                if not m["snapshot_taken"]:
                    if st.button("Snapshot", key=f"snap_{m['id']}"):
                        try:
                            db.capture_milestone_snapshot(conn, m["id"])
                            st.success(f"Snapshot taken for '{m['name']}'.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
            with col_del:
                if st.button("Delete", key=f"del_milestone_{m['id']}"):
                    try:
                        db.delete_milestone(conn, m["id"])
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
    else:
        st.info("No milestones yet.")

    st.divider()

    # ---- Section: Player Database ----
    st.subheader("Player Database")
    st.caption("Remove all players from the database and clear every roster. Use before starting a fresh game.")

    if st.button("Clear All Players", type="primary"):
        st.session_state["confirm_clear_players"] = True

    if st.session_state.get("confirm_clear_players"):
        st.warning("This will delete **all players** and **clear every roster**. This cannot be undone.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Yes, clear everything"):
                count = db.clear_all_players(conn)
                st.success(f"Deleted {count} player{'s' if count != 1 else ''} and cleared all rosters.")
                del st.session_state["confirm_clear_players"]
                st.rerun()
        with col_no:
            if st.button("Cancel", key="cancel_clear_players"):
                del st.session_state["confirm_clear_players"]
                st.rerun()

    st.divider()

    # ---- Section: Draft ----
    st.subheader("Draft")

    active_draft = db.get_active_draft(conn)

    if active_draft:
        # Show current draft status with option to end early
        base_order = db.get_draft_order(conn, active_draft["id"])
        current_drafter_id = db.get_current_drafter_id(conn, active_draft["id"])
        current_name = next(
            (p["participant_name"] for p in base_order if p["participant_id"] == current_drafter_id),
            "—"
        )

        st.info(
            f"**Round {active_draft['current_round']} of {active_draft['num_rounds']}**  \n"
            f"Currently picking: **{current_name}**  \n"
            f"Snake: {'Yes' if active_draft['snake'] else 'No'} · "
            f"Budget bonus applied: {fmt_euros(active_draft['budget_bonus'])}"
        )

        st.write("**Base pick order:**")
        for entry in base_order:
            st.write(f"{entry['position']}. {entry['participant_name']}")

        if st.button("End Draft Early", type="primary"):
            db.complete_draft(conn, active_draft["id"])
            st.success("Draft ended.")
            st.rerun()

    else:
        # No active draft — show initiation form
        st.caption("Configure and start a draft. The market will lock until the draft ends.")

        if not participants:
            st.info("Add participants before initiating a draft.")
        else:
            with st.form("initiate_draft"):
                d_rounds = st.number_input("Number of rounds", min_value=1, max_value=100, value=3)
                d_snake  = st.checkbox("Snake draft (reverse order each round)")
                d_bonus  = st.number_input(
                    "Budget bonus per participant (€)",
                    min_value=0,
                    max_value=1_000_000_000,
                    value=0,
                    step=1_000_000,
                )

                st.write("**Assign draft positions** (1 = picks first):")
                position_inputs = {}
                for p in participants:
                    position_inputs[p["id"]] = st.number_input(
                        p["name"],
                        min_value=1,
                        max_value=len(participants),
                        value=1,
                        key=f"draft_pos_{p['id']}",
                    )

                if st.form_submit_button("Initiate Draft"):
                    # Validate: all positions must be unique and cover 1..N
                    assigned = list(position_inputs.values())
                    if len(set(assigned)) != len(participants):
                        st.error("Each participant must have a unique draft position.")
                    elif sorted(assigned) != list(range(1, len(participants) + 1)):
                        st.error(f"Positions must cover 1 to {len(participants)} with no gaps.")
                    else:
                        # Sort participants by their assigned position
                        ordered_ids = [
                            pid for pid, _ in
                            sorted(position_inputs.items(), key=lambda x: x[1])
                        ]
                        try:
                            db.create_draft(
                                conn,
                                num_rounds=int(d_rounds),
                                snake=d_snake,
                                budget_bonus=int(d_bonus),
                                ordered_participant_ids=ordered_ids,
                            )
                            st.success("Draft initiated! The market is now locked.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Could not initiate draft: {e}")


# ---------------------------------------------------------------------------
# Render the selected page
# ---------------------------------------------------------------------------

if page == "Leaderboard":
    page_leaderboard()
elif page == "My Team":
    page_my_team()
elif page == "Market":
    page_market()
elif page == "Milestones":
    page_milestones()
elif page == "Admin":
    page_admin()
