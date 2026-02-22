"""
app.py — Streamlit UI for the Transfer Market Fantasy Game.

Run with:
    uv run streamlit run app.py

Four pages (Admin is only visible to admin users):
  1. Leaderboard  — standings ranked by total assets (visible to all)
  2. My Team      — view and manage your own roster
  3. Market       — buy available players or add a new one by Transfermarkt URL
  4. Admin        — manage participants and user accounts (admin only)
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
    if st.button("Log out", use_container_width=True):
        del st.session_state["user"]
        st.rerun()

    st.divider()

    pages = ["Leaderboard", "My Team", "Market"]
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

    st.divider()

    # --- Section 1: Buy an available player (already in the DB) ---
    st.subheader("Available Players")
    available = db.get_available_players(conn)

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
                if st.button(
                    "Buy",
                    key=f"buy_{player['id']}",
                    disabled=not affordable,
                    help=None if affordable else "Insufficient budget",
                ):
                    try:
                        updated = db.buy_existing_player(conn, participant_id, player["id"])
                        st.success(
                            f"Bought {player['name']} for {fmt_euros(player['current_value'])}. "
                            f"New budget: {fmt_euros(updated['budget'])}"
                        )
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
    else:
        st.info("No players currently available. Add a new player below.")

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
# Page: Admin (admin users only)
# ---------------------------------------------------------------------------

def page_admin():
    if not auth.is_admin():
        st.error("You do not have permission to view this page.")
        return

    st.header("Admin")

    # ---- Section: Participants ----
    st.subheader("Participants")
    st.caption("Add the game participants and their starting budgets.")

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
            st.write(f"- **{p['name']}** — budget: {fmt_euros(p['budget'])}")
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
        new_username     = st.text_input("Username")
        new_password     = st.text_input("Password", type="password")
        new_is_admin     = st.checkbox("Admin privileges")
        linked_name      = st.selectbox("Link to participant", options=list(participant_options.keys()))

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
                role = "Admin" if u["is_admin"] else "Basic"
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


# ---------------------------------------------------------------------------
# Render the selected page
# ---------------------------------------------------------------------------

if page == "Leaderboard":
    page_leaderboard()
elif page == "My Team":
    page_my_team()
elif page == "Market":
    page_market()
elif page == "Admin":
    page_admin()
