"""
app.py — Streamlit UI for the Transfer Market Fantasy Game.

Run with:
    uv run streamlit run app.py

Three pages:
  1. Leaderboard  — standings ranked by total assets
  2. My Team      — view roster, sell players
  3. Market       — buy available players or add a new one by Transfermarkt URL
"""

import os
import streamlit as st
from dotenv import load_dotenv

import db
import scraper

load_dotenv()


# ---------------------------------------------------------------------------
# Shared database connection (one connection for the whole app session)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_conn():
    # st.secrets is populated from the Streamlit Community Cloud dashboard in
    # production, and from .streamlit/secrets.toml locally (if present).
    # Falls back to the .env file for local development via load_dotenv() above.
    url = st.secrets.get("DATABASE_URL") or os.environ["DATABASE_URL"]
    return db.get_connection(url)


conn = get_conn()


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
        st.info("No participants yet. Add some participants to get started.")
        return

    # Build a display table
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

    participants = db.get_participants(conn)
    if not participants:
        st.info("No participants yet.")
        return

    # Participant selector
    names = {p["name"]: p["id"] for p in participants}
    selected_name = st.selectbox("Select your name", options=list(names.keys()))
    participant_id = names[selected_name]

    participant = db.get_participant(conn, participant_id)
    roster = db.get_roster(conn, participant_id)

    # Summary stats
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

    # Display each player with a sell button
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

    participants = db.get_participants(conn)
    if not participants:
        st.info("No participants yet.")
        return

    names = {p["name"]: p["id"] for p in participants}
    selected_name = st.selectbox("Buying as", options=list(names.keys()))
    participant_id = names[selected_name]
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
                    # Store in session state so the confirm step can use it
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
# Page: Admin — add participants
# ---------------------------------------------------------------------------

def page_admin():
    st.header("Admin")
    st.caption("Add participants before the game starts.")

    with st.form("add_participant"):
        name = st.text_input("Participant name")
        budget = st.number_input(
            "Starting budget (€)",
            min_value=1_000_000,
            max_value=1_000_000_000,
            value=100_000_000,
            step=5_000_000,
        )
        submitted = st.form_submit_button("Add Participant")

        if submitted:
            if not name.strip():
                st.error("Name cannot be empty.")
            else:
                try:
                    db.create_participant(conn, name.strip(), int(budget))
                    st.success(f"Added participant '{name}' with budget {fmt_euros(int(budget))}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not add participant: {e}")

    st.divider()
    st.subheader("Current Participants")
    participants = db.get_participants(conn)
    if participants:
        for p in participants:
            st.write(f"- **{p['name']}** — budget: {fmt_euros(p['budget'])}")
    else:
        st.info("No participants yet.")


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Transfer Market Game", page_icon="⚽", layout="wide")
st.title("⚽ Transfer Market Fantasy Game")

page = st.sidebar.radio(
    "Navigate",
    ["Leaderboard", "My Team", "Market", "Admin"],
)

if page == "Leaderboard":
    page_leaderboard()
elif page == "My Team":
    page_my_team()
elif page == "Market":
    page_market()
elif page == "Admin":
    page_admin()
