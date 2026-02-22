from __future__ import annotations

"""
auth.py — Authentication helpers for the Transfer Market Fantasy Game.

Passwords are hashed with bcrypt and stored in the `users` table.
Login state is kept in Streamlit session state under the key "user".

Session state shape after login:
    st.session_state["user"] = {
        "id":             int,
        "username":       str,
        "is_admin":       bool,
        "participant_id": int | None,  # None for admin accounts not linked to a team
    }
"""

import bcrypt
import streamlit as st


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    """Return True if the plaintext matches the bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# Database queries  (all accept an open psycopg2 connection)
# ---------------------------------------------------------------------------

def get_user_by_username(conn, username: str) -> dict | None:
    """Look up a user by username. Returns None if not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, username, password_hash, is_admin, participant_id "
            "FROM users WHERE username = %s",
            (username,)
        )
        return cur.fetchone()


def get_all_users(conn) -> list[dict]:
    """Return all users ordered by username, with their linked participant name."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.username, u.is_admin, u.participant_id,
                   p.name AS participant_name, u.created_at
            FROM users u
            LEFT JOIN participants p ON p.id = u.participant_id
            ORDER BY u.username
        """)
        return cur.fetchall()


def create_user(conn, username: str, password: str,
                is_admin: bool = False, participant_id: int | None = None) -> dict:
    """
    Insert a new user and return the created row.

    Args:
        username:       Unique display name used to log in.
        password:       Plaintext password (will be hashed before storing).
        is_admin:       Whether this user has admin privileges.
        participant_id: The participant this user is linked to, or None.

    Raises:
        psycopg2.errors.UniqueViolation: if the username is already taken.
    """
    password_hash = hash_password(password)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (username, password_hash, is_admin, participant_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id, username, is_admin, participant_id
        """, (username, password_hash, is_admin, participant_id))
        row = cur.fetchone()
    conn.commit()
    return row


def delete_user(conn, user_id: int) -> None:
    """Delete a user account. Does not affect the linked participant."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()


def update_user_participant(conn, user_id: int, participant_id: int | None) -> None:
    """
    Update the participant linked to a user account.

    Passing None unlinks the user from any participant.

    Raises:
        psycopg2.errors.UniqueViolation: if participant_id is already linked
        to a different user account (enforced by the UNIQUE constraint).
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET participant_id = %s WHERE id = %s",
            (participant_id, user_id)
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def current_user() -> dict | None:
    """Return the currently logged-in user dict, or None if not logged in."""
    return st.session_state.get("user")


def is_admin() -> bool:
    """Return True if the currently logged-in user has admin privileges."""
    user = current_user()
    return bool(user and user["is_admin"])


# ---------------------------------------------------------------------------
# Login UI
# ---------------------------------------------------------------------------

def require_login(conn) -> None:
    """
    Gate the entire app behind a login form.

    If the user is already logged in (session state has "user"), this is a
    no-op and the caller can proceed normally.

    If not logged in, renders a centered login form and calls st.stop() so
    nothing below this call is executed. On successful login the user dict is
    written to session state and the page reruns.
    """
    if current_user():
        return  # Already authenticated — let the app render normally

    # Center the login form with empty columns either side
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.subheader("Login")
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", use_container_width=True)

        if submitted:
            user = get_user_by_username(conn, username)
            if user and check_password(password, user["password_hash"]):
                # Store a plain dict (not a RealDictRow) in session state
                st.session_state["user"] = {
                    "id":             user["id"],
                    "username":       user["username"],
                    "is_admin":       user["is_admin"],
                    "participant_id": user["participant_id"],
                }
                st.rerun()
            else:
                st.error("Invalid username or password.")

    st.stop()
