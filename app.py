"""
app.py — Streamlit UI for the Transfer Market Fantasy Game.

Run with:
    uv run streamlit run app.py

Six pages (Admin is only visible to admin users):
  1. Leaderboard  — standings ranked by total assets (visible to all)
  2. My Team      — view and manage your own roster
  3. Market       — buy available players or add a new one by Transfermarkt URL
  4. Milestones   — upcoming milestone date and historical milestone results
  5. Feed         — dated log of all transfers across participants
  6. Admin        — manage participants, users, and milestones (admin only)
"""

import os
import streamlit as st
from dotenv import load_dotenv

import db
import auth
import scraper

load_dotenv()

# Expose Kaggle credentials from st.secrets as env vars so the kaggle library
# can authenticate. st.secrets doesn't set os.environ automatically.
for _kaggle_key in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
    if _kaggle_key not in os.environ:
        try:
            os.environ[_kaggle_key] = st.secrets[_kaggle_key]
        except Exception:
            pass  # Not set in secrets — will fall back to ~/.kaggle/kaggle.json or fail at auth time

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

st.title("Footy Ball Money Man Fantasy Game")
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

    # Show a badge on the Offers page if there are pending incoming offers
    _pending_offer_count = 0
    if user.get("participant_id"):
        _pending_offer_count = db.count_pending_offers_received(conn, user["participant_id"])
    offers_label = f"Offers ({_pending_offer_count})" if _pending_offer_count else "Offers"

    pages = ["Leaderboard", "My Team", "Market", offers_label, "Milestones", "Feed"]
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


def fmt_delta(value: int) -> str:
    """Format a signed euro delta, e.g. +€5.5M or -€3M."""
    abs_str = fmt_euros(abs(value))
    if value > 0:
        return f"+{abs_str}"
    if value < 0:
        return f"-{abs_str}"
    return abs_str


def colored_delta(value: int) -> str:
    """Return an HTML span with green/red/grey coloring for a euro delta."""
    color = "#2ea043" if value > 0 else "#f85149" if value < 0 else "#888888"
    return f'<span style="color:{color}">{fmt_delta(value)}</span>'


def player_subtitle(player: dict) -> str:
    """
    Build a 'Club · Position' subtitle, omitting fields that are missing or
    uninformative (empty string, None, or the literal 'Unknown').
    """
    club     = player.get("club")     or ""
    position = player.get("position") or ""
    if club == "Unknown":     club = ""
    if position == "Unknown": position = ""
    parts = [p for p in (club, position) if p]
    return " · ".join(parts)


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

    last_milestone_info, last_player_values = db.get_last_milestone_player_values(conn)
    if last_milestone_info:
        milestone_label = f"{last_milestone_info['name']} ({last_milestone_info['date'].strftime('%d %b %Y')})"
        st.caption(f"Value changes shown vs last milestone: **{milestone_label}**")

    for row in rows:
        roster = db.get_roster(conn, row["id"])
        label = f"**{row['name']}** — {len(roster)} player{'s' if len(roster) != 1 else ''}"
        with st.expander(label):
            if roster:
                for player in roster:
                    vs_purchase = player["current_value"] - player["purchased_at_value"]
                    purchase_str = (
                        f" (+{fmt_euros(vs_purchase)})" if vs_purchase > 0
                        else f" ({fmt_euros(vs_purchase)})" if vs_purchase < 0
                        else ""
                    )

                    milestone_html = ""
                    if last_milestone_info and player["id"] in last_player_values:
                        delta = player["current_value"] - last_player_values[player["id"]]
                        milestone_html = f" · vs milestone: {colored_delta(delta)}"
                    elif last_milestone_info:
                        milestone_html = ' · vs milestone: <span style="color:#888888">New</span>'

                    _sub = player_subtitle(player)
                    st.markdown(
                        f"**{player['name']}**{(' — ' + _sub) if _sub else ''}  \n"
                        f"Value: **{fmt_euros(player['current_value'])}**{purchase_str}"
                        f"{milestone_html} · Paid: {fmt_euros(player['purchased_at_value'])}",
                        unsafe_allow_html=True,
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

    # Fetch last milestone snapshot for delta metrics
    last_snap = db.get_last_milestone_participant_snapshot(conn, participant_id)

    col1, col2, col3 = st.columns(3)

    if last_snap:
        budget_delta = participant["budget"] - last_snap["budget"] if last_snap["budget"] is not None else None
        team_delta   = team_value - last_snap["team_value"]

        budget_delta_str = (f"+{fmt_euros(budget_delta)}" if budget_delta > 0 else f"-{fmt_euros(abs(budget_delta))}") if budget_delta is not None else None
        team_delta_str   = f"+{fmt_euros(team_delta)}" if team_delta > 0 else f"-{fmt_euros(abs(team_delta))}"

        col1.metric("Budget Remaining", fmt_euros(participant["budget"]), delta=budget_delta_str)
        col2.metric("Team Value",       fmt_euros(team_value),            delta=team_delta_str)
    else:
        col1.metric("Budget Remaining", fmt_euros(participant["budget"]))
        col2.metric("Team Value",       fmt_euros(team_value))

    col3.metric("Players", f"{len(roster)} / {db.MAX_ROSTER_SIZE}")

    if last_snap:
        milestone_label = f"{last_snap['milestone_name']} ({last_snap['milestone_date'].strftime('%d %b %Y')})"
        st.caption(f"Deltas vs last milestone: **{milestone_label}**")

    st.divider()

    if not roster:
        st.info("Your roster is empty. Head to the Market to buy players.")
        return

    st.subheader("Roster")

    # Fetch per-player milestone values for change-since-milestone display
    last_milestone_info, last_player_values = db.get_last_milestone_player_values(conn)

    for player in roster:
        col_info, col_btn = st.columns([4, 1])
        with col_info:
            vs_purchase = player["current_value"] - player["purchased_at_value"]
            purchase_str = (
                f"  (+{fmt_euros(vs_purchase)})" if vs_purchase > 0
                else f"  ({fmt_euros(vs_purchase)})" if vs_purchase < 0
                else ""
            )

            milestone_html = ""
            if last_milestone_info and player["id"] in last_player_values:
                delta = player["current_value"] - last_player_values[player["id"]]
                milestone_html = f"  · vs milestone: {colored_delta(delta)}"
            elif last_milestone_info:
                milestone_html = '  · vs milestone: <span style="color:#888888">New</span>'

            _sub = player_subtitle(player)
            st.markdown(
                f"**{player['name']}**{(' — ' + _sub) if _sub else ''}  \n"
                f"Current value: **{fmt_euros(player['current_value'])}**{purchase_str}"
                f"{milestone_html}  \n"
                f"Purchased at: {fmt_euros(player['purchased_at_value'])}",
                unsafe_allow_html=True,
            )
        with col_btn:
            confirm_key = f"confirm_sell_{player['id']}"
            if not st.session_state.get(confirm_key):
                if st.button("Sell", key=f"sell_{player['id']}"):
                    st.session_state[confirm_key] = True
                    st.rerun()

        if st.session_state.get(f"confirm_sell_{player['id']}"):
            fee = round(player["current_value"] * 0.05)
            net = player["current_value"] - fee
            st.warning(
                f"Selling to the market incurs a 5% fee ({fmt_euros(fee)}). "
                f"You will receive **{fmt_euros(net)}** instead of the full {fmt_euros(player['current_value'])}. "
                f"Direct offers between managers are not subject to this fee."
            )
            col_confirm, col_cancel = st.columns(2)
            with col_confirm:
                if st.button("Confirm Sale", key=f"confirm_btn_{player['id']}"):
                    try:
                        updated = db.sell_player(conn, participant_id, player["id"])
                        st.session_state[f"confirm_sell_{player['id']}"] = False
                        st.success(
                            f"Sold {player['name']} for {fmt_euros(net)}. "
                            f"New budget: {fmt_euros(updated['budget'])}"
                        )
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
            with col_cancel:
                if st.button("Cancel", key=f"cancel_sell_{player['id']}"):
                    st.session_state[f"confirm_sell_{player['id']}"] = False
                    st.rerun()


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
        if active_draft["snake"] and active_draft["current_round"] % 2 == 0:
            round_order = list(reversed(base_order))
        else:
            round_order = list(base_order)

        current_drafter_name = next(
            (p["participant_name"] for p in base_order if p["participant_id"] == current_drafter_id),
            "Unknown"
        )

        pick_order_str = " → ".join(
            f"**{p['participant_name']}**" if p["participant_id"] == current_drafter_id
            else p["participant_name"]
            for p in round_order
        )

        if my_turn:
            st.success(
                f"### YOUR TURN TO PICK!\n"
                f"**Round {active_draft['current_round']} of {active_draft['num_rounds']}**  \n"
                f"Pick order: {pick_order_str}"
            )
        else:
            st.error(
                f"### NOT YOUR TURN\n"
                f"Waiting for **{current_drafter_name}** to pick.  \n"
                f"**Round {active_draft['current_round']} of {active_draft['num_rounds']}** — "
                f"Pick order: {pick_order_str}"
            )

    st.divider()

    # --- Section 1: Buy an available player (already in the DB) ---
    st.subheader("Available Players")

    # During a draft, only the current drafter may do anything in the market.
    if active_draft and not my_turn:
        current_drafter_name = next(
            (p["participant_name"] for p in base_order if p["participant_id"] == current_drafter_id),
            "Unknown"
        )
        st.info(f"Player purchases are locked until it is your turn. Currently waiting for **{current_drafter_name}**.")
        return

    available = db.get_available_players(conn)

    if available:
        for player in available:
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                _sub = player_subtitle(player)
                st.markdown(
                    f"**{player['name']}**{(' — ' + _sub) if _sub else ''}  \n"
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
                    # Server-side turn guard: re-check current drafter at purchase time
                    if active_draft and db.get_current_drafter_id(conn, active_draft["id"]) != participant_id:
                        st.error("It is no longer your turn. Please wait.")
                        st.rerun()
                    else:
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
                # Server-side turn guard: re-check current drafter at purchase time
                if active_draft and db.get_current_drafter_id(conn, active_draft["id"]) != participant_id:
                    st.error("It is no longer your turn. Please wait.")
                    st.rerun()
                else:
                    try:
                        updated = db.buy_new_player(conn, participant_id, data, source=scraper.DATA_SOURCE)
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

            if milestone.get("show_portfolio_value"):
                row["Portfolio Value"] = fmt_euros(r["portfolio_value"])

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
# Page: Offers — direct trade offers between participants
# ---------------------------------------------------------------------------

def _fmt_offer_side(players: list[dict], money: int) -> str:
    """Return a human-readable summary of one side of a trade offer.

    Player dicts from trade_offer_players use player_name / player_club /
    player_position keys (denormalised), so we map them before calling
    player_subtitle.
    """
    parts = []
    for p in players:
        # trade_offer_players dicts use player_* keys; map to the keys
        # player_subtitle expects (club, position)
        subtitle_dict = {
            "club":     p.get("player_club"),
            "position": p.get("player_position"),
        }
        sub = player_subtitle(subtitle_dict)
        parts.append(f"**{p['player_name']}**" + (f" ({sub})" if sub else ""))
    if money:
        parts.append(fmt_euros(money))
    return ", ".join(parts) if parts else "nothing"


def page_offers():
    st.header("Offers")

    participant_id = user["participant_id"]
    if not participant_id:
        st.info("Your account is not linked to a participant. Ask the admin to link your account to your team.")
        return

    # Block during an active draft
    active_draft = db.get_active_draft(conn)
    if active_draft:
        st.warning("Trade offers are paused while a draft is active.")
        return

    participant = db.get_participant(conn, participant_id)

    # ------------------------------------------------------------------ #
    # Section 1: Received offers (pending)
    # ------------------------------------------------------------------ #
    st.subheader("Received Offers")
    received = db.get_trade_offers_received(conn, participant_id)

    if not received:
        st.info("No pending offers.")
    else:
        for offer in received:
            sg_str = _fmt_offer_side(offer["sender_gives"],   offer["sender_money"])
            rg_str = _fmt_offer_side(offer["receiver_gives"], offer["receiver_money"])
            with st.container(border=True):
                st.markdown(
                    f"**{offer['sender_name']}** wants to trade with you  \n"
                    f"They give: {sg_str}  \n"
                    f"You give: {rg_str}"
                )
                col_acc, col_dec = st.columns(2)
                with col_acc:
                    if st.button("Accept", key=f"accept_{offer['id']}", type="primary"):
                        try:
                            db.accept_trade_offer(conn, offer["id"], participant_id)
                            st.success("Trade accepted!")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                with col_dec:
                    if st.button("Decline", key=f"decline_{offer['id']}"):
                        try:
                            db.decline_trade_offer(conn, offer["id"], participant_id)
                            st.success("Offer declined.")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))

    st.divider()

    # ------------------------------------------------------------------ #
    # Section 2: Sent offers
    # ------------------------------------------------------------------ #
    st.subheader("My Sent Offers")
    sent = db.get_trade_offers_sent(conn, participant_id)
    pending_sent = [o for o in sent if o["status"] == "pending"]

    if not pending_sent:
        st.info("No pending sent offers.")
    else:
        for offer in pending_sent:
            sg_str = _fmt_offer_side(offer["sender_gives"],   offer["sender_money"])
            rg_str = _fmt_offer_side(offer["receiver_gives"], offer["receiver_money"])
            with st.container(border=True):
                st.markdown(
                    f"To **{offer['receiver_name']}** — awaiting response  \n"
                    f"You give: {sg_str}  \n"
                    f"They give: {rg_str}"
                )
                if st.button("Cancel offer", key=f"cancel_{offer['id']}"):
                    try:
                        db.cancel_trade_offer(conn, offer["id"], participant_id)
                        st.success("Offer cancelled.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    st.divider()

    # ------------------------------------------------------------------ #
    # Section 3: Make an offer
    # ------------------------------------------------------------------ #
    st.subheader("Make an Offer")

    all_participants = db.get_participants(conn)
    others = [p for p in all_participants if p["id"] != participant_id]

    if not others:
        st.info("No other participants to trade with yet.")
        return

    my_roster = db.get_roster(conn, participant_id)

    target_options = {p["name"]: p["id"] for p in others}
    target_name = st.selectbox("Send offer to", options=list(target_options.keys()))
    target_id = target_options[target_name]
    their_roster = db.get_roster(conn, target_id)

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(f"**You give** (your team)")
        my_player_options = {
            f"{p['name']} ({fmt_euros(p['current_value'])})": p["id"]
            for p in my_roster
        }
        my_selected_labels = st.multiselect(
            "Players you're offering",
            options=list(my_player_options.keys()),
            key="offer_my_players",
        )
        my_selected_ids = [my_player_options[n] for n in my_selected_labels]
        sender_money = st.number_input(
            "Money you're giving (€)",
            min_value=0,
            max_value=int(participant["budget"]),
            value=0,
            step=1_000_000,
            key="offer_sender_money",
        )

    with col_right:
        st.markdown(f"**You receive** (from {target_name})")
        their_player_options = {
            f"{p['name']} ({fmt_euros(p['current_value'])})": p["id"]
            for p in their_roster
        }
        their_selected_labels = st.multiselect(
            "Players you're requesting",
            options=list(their_player_options.keys()),
            key="offer_their_players",
        )
        their_selected_ids = [their_player_options[n] for n in their_selected_labels]
        receiver_money = st.number_input(
            "Money you're requesting (€)",
            min_value=0,
            value=0,
            step=1_000_000,
            key="offer_receiver_money",
        )

    offer_is_empty = (
        not my_selected_ids and not their_selected_ids
        and sender_money == 0 and receiver_money == 0
    )

    if st.button(
        f"Send Offer to {target_name}",
        disabled=offer_is_empty,
        help="Add at least one player or a money amount to the offer." if offer_is_empty else None,
    ):
        try:
            db.create_trade_offer(
                conn,
                sender_id=participant_id,
                receiver_id=target_id,
                sender_money=int(sender_money),
                receiver_money=int(receiver_money),
                sender_player_ids=my_selected_ids,
                receiver_player_ids=their_selected_ids,
            )
            st.success(f"Offer sent to {target_name}!")
            st.rerun()
        except ValueError as e:
            st.error(str(e))


# ---------------------------------------------------------------------------
# Page: Feed — dated log of all transfers across participants
# ---------------------------------------------------------------------------

def page_feed():
    st.header("Activity Feed")
    st.caption("A live log of all transfers and trade offers.")

    from collections import defaultdict
    import datetime

    events = db.get_combined_feed(conn)

    if not events:
        st.info("No activity yet. Moves will appear here once players are bought, sold, or traded.")
        return

    # Group entries by date for a clean dated timeline
    grouped: dict[datetime.date, list] = defaultdict(list)
    for e in events:
        day = e["event_time"].date()
        grouped[day].append(e)

    for day in sorted(grouped.keys(), reverse=True):
        st.subheader(day.strftime("%-d %B %Y"))
        for e in grouped[day]:
            time_str = e["event_time"].strftime("%H:%M")

            col1, col2 = st.columns([5, 1])
            with col2:
                st.markdown(
                    f"<span style='color:gray;font-size:0.85em'>{time_str}</span>",
                    unsafe_allow_html=True,
                )

            with col1:
                if e["event_type"] == "buy_sell":
                    is_buy = e["transfer_type"] == "buy"
                    action_icon = "🟢" if is_buy else "🔴"
                    action_word = "bought" if is_buy else "sold"
                    value_str = fmt_euros(e["value"])
                    subtitle = f"{e['player_club']} · {e['player_position']}"
                    st.markdown(
                        f"{action_icon} **{e['participant_name']}** {action_word} "
                        f"**{e['player_name']}** <span style='color:gray;font-size:0.85em'>({subtitle})</span> "
                        f"for **{value_str}**",
                        unsafe_allow_html=True,
                    )

                else:  # trade_offer
                    status = e["status"]
                    sender   = e["sender_name"]
                    receiver = e["receiver_name"]
                    sg_str = _fmt_offer_side(e.get("sender_gives", []),   e["sender_money"])
                    rg_str = _fmt_offer_side(e.get("receiver_gives", []), e["receiver_money"])

                    if status == "pending":
                        st.markdown(
                            f"🔄 **{sender}** proposed a trade with **{receiver}**  \n"
                            f"<span style='color:gray;font-size:0.85em'>"
                            f"{sender} gives: {sg_str} · {receiver} gives: {rg_str} · "
                            f"<em>Awaiting response</em></span>",
                            unsafe_allow_html=True,
                        )
                    elif status == "accepted":
                        st.markdown(
                            f"✅ **{sender}** and **{receiver}** completed a trade  \n"
                            f"<span style='color:gray;font-size:0.85em'>"
                            f"{sender} gave: {sg_str} · {receiver} gave: {rg_str}</span>",
                            unsafe_allow_html=True,
                        )

        st.divider()


# ---------------------------------------------------------------------------
# Page: Admin (admin users only)
# ---------------------------------------------------------------------------

def page_admin():
    if not auth.is_admin():
        st.error("You do not have permission to view this page.")
        return

    st.header("Admin")

    # ---- Section: My Account ----
    st.subheader("My Account")
    st.caption("Link your admin account to a participant so you can manage your own team.")

    participants = db.get_participants(conn)
    all_users = auth.get_all_users(conn)

    # Build options: None + any participant not linked to a DIFFERENT user
    other_linked_ids = {
        u["participant_id"] for u in all_users
        if u["participant_id"] and u["id"] != user["id"]
    }
    linkable = [p for p in participants if p["id"] not in other_linked_ids]
    link_options = {"(None — no team)": None} | {p["name"]: p["id"] for p in linkable}

    current_participant_id = user["participant_id"]
    current_label = next(
        (p["name"] for p in participants if p["id"] == current_participant_id),
        "(None — no team)",
    )

    with st.form("my_account_form"):
        selected_label = st.selectbox(
            "Linked participant",
            options=list(link_options.keys()),
            index=list(link_options.keys()).index(current_label)
                  if current_label in link_options else 0,
        )
        if st.form_submit_button("Save"):
            new_participant_id = link_options[selected_label]
            try:
                auth.update_user_participant(conn, user["id"], new_participant_id)
                # Keep session state in sync so My Team works immediately
                st.session_state["user"]["participant_id"] = new_participant_id
                st.success(
                    f"Linked to '{selected_label}'." if new_participant_id
                    else "Unlinked from participant."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Could not update account: {e}")

    st.divider()

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

    # Re-fetch participants to include any just-added participant
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

    # ---- Section: Budget Adjustments ----
    st.subheader("Budget Adjustments")
    st.caption("Apply a one-time bonus or penalty to a participant's budget.")

    if participants:
        with st.form("budget_adjustment_form"):
            participant_options = {p["name"]: p["id"] for p in participants}
            selected_name = st.selectbox("Participant", options=list(participant_options.keys()))
            amount = st.number_input(
                "Amount (€) — positive to add, negative to deduct",
                min_value=-500_000_000,
                max_value=500_000_000,
                value=0,
                step=1_000_000,
            )
            reason = st.text_input("Reason (optional)", placeholder="e.g. Performance bonus, rule violation penalty")
            if st.form_submit_button("Apply Adjustment"):
                if amount == 0:
                    st.error("Amount cannot be zero.")
                else:
                    try:
                        updated = db.adjust_participant_budget(
                            conn, participant_options[selected_name], int(amount)
                        )
                        action = "bonus" if amount > 0 else "penalty"
                        detail = f" ({reason})" if reason.strip() else ""
                        st.success(
                            f"Applied {fmt_euros(abs(int(amount)))} {action} to "
                            f"**{updated['name']}**{detail}. "
                            f"New budget: {fmt_euros(updated['budget'])}."
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not apply adjustment: {e}")
    else:
        st.info("No participants yet.")

    st.divider()

    # ---- Section: User Accounts ----
    st.subheader("User Accounts")
    st.caption("Create login accounts and link them to participants.")

    # Map participant name -> id for the dropdown (only unlinked participants)
    # Re-fetch all_users to reflect any accounts added earlier in this render
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

    # ---- Assign / Unassign participant for an existing user ----
    if all_users and participants:
        st.write("")
        st.write("**Assign / Unassign Participant**")

        assign_user_options = {
            f"{u['username']} ({u['participant_name'] or 'no team'})": u["id"]
            for u in all_users
        }
        all_participant_options = {"(None — no team)": None} | {
            p["name"]: p["id"] for p in participants
        }

        with st.form("assign_participant_form"):
            selected_user_label = st.selectbox(
                "User", options=list(assign_user_options.keys())
            )
            selected_participant_label = st.selectbox(
                "Participant", options=list(all_participant_options.keys())
            )
            if st.form_submit_button("Assign"):
                target_user_id = assign_user_options[selected_user_label]
                new_pid = all_participant_options[selected_participant_label]
                try:
                    auth.update_user_participant(conn, target_user_id, new_pid)
                    st.success(
                        f"Linked to '{selected_participant_label}'."
                        if new_pid else "Unlinked from participant."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not update: {e}")

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
        m_portfolio = st.checkbox("Total portfolio value (team value + unspent budget)", value=True)
        m_total  = st.checkbox("Total team value")
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
                        show_portfolio_value=m_portfolio,
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
                if m.get("show_portfolio_value"): metrics.append("Portfolio value")
                if m["show_total_value"]:         metrics.append("Total value")
                if m["show_value_change"]:        metrics.append("Value change")
                if m["show_pct_change"]:          metrics.append("% change")
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

    # ---- Section: Market Values ----
    st.subheader("Market Values")

    data_source = st.radio(
        "Data source",
        options=["ceapi", "kaggle", "transfermarkt"],
        index=0,
        horizontal=True,
        help=(
            "**ceapi** — fetches real-time values from Transfermarkt's internal JSON API. "
            "No Cloudflare bot detection. Recommended.\n\n"
            "**Kaggle** — uses the weekly community dataset. Reliable but values lag by days/weeks.\n\n"
            "**Transfermarkt** — scrapes the HTML page directly. May return 403 on cloud IPs."
        ),
    )
    scraper.set_data_source(data_source)

    if data_source == "ceapi":
        st.caption("Using Transfermarkt's internal API. Values are real-time.")
    elif data_source == "kaggle":
        st.caption("Using the Kaggle dataset. Values reflect the most recent weekly update.")
    else:
        st.caption(
            "Scraping Transfermarkt directly. "
            "Requests are spaced 15–45 seconds apart — expect roughly 30 seconds per player."
        )

    all_players = db.get_all_players(conn)

    if not all_players:
        st.info("No players in the database yet.")
    else:
        last_updated = max(
            (p["last_updated"] for p in all_players if p["last_updated"]),
            default=None,
        )
        if last_updated:
            st.caption(f"Last updated: {last_updated.strftime('%d %b %Y at %H:%M UTC')}")

        if st.button(f"Refresh All Player Values ({len(all_players)} players)"):
            refresh_results = []

            def _on_player_done(idx, total, result):
                refresh_results.append(result)
                progress_bar.progress(idx / total)
                if result["success"]:
                    status_log.write(f"✓ [{idx}/{total}] {result['name']}")
                else:
                    status_log.write(f"✗ [{idx}/{total}] {result['name']}: {result['error']}")

            with st.status(f"Refreshing {len(all_players)} player(s)...", expanded=True) as status_box:
                status_log = st.empty()
                progress_bar = st.progress(0)
                scraper.refresh_all_player_values(conn, on_player_done=_on_player_done)
                status_box.update(label="Refresh complete.", state="complete", expanded=False)

            successes = sum(1 for r in refresh_results if r["success"])
            failures = len(refresh_results) - successes
            if failures:
                st.warning(f"Updated {successes} player(s). {failures} failed.")
            else:
                st.success(f"All {successes} player(s) updated successfully.")

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
            marker = " ← current" if entry["participant_id"] == current_drafter_id else ""
            st.write(f"{entry['position']}. {entry['participant_name']}{marker}")

        col_skip, col_end = st.columns(2)
        with col_skip:
            if st.button("Force Skip Turn", help="Advance to the next participant without a pick being made."):
                db.advance_draft(conn, active_draft["id"])
                st.success("Skipped to the next participant.")
                st.rerun()
        with col_end:
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
elif page in ("Offers", offers_label):
    page_offers()
elif page == "Milestones":
    page_milestones()
elif page == "Feed":
    page_feed()
elif page == "Admin":
    page_admin()
