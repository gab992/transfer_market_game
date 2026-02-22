"""
create_admin.py — One-time bootstrap script to create the first admin account.

Run this once after setting up the database:
    uv run python create_admin.py

This is the only way to create an admin user. All subsequent user management
(including creating more users) is done through the Admin page in the app.
"""

import os
import getpass
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import auth

load_dotenv()


def main():
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor
    )

    print("Create Admin Account")
    print("--------------------")
    username = input("Username: ").strip()
    if not username:
        print("Error: username cannot be empty.")
        return

    password = getpass.getpass("Password: ")
    if not password:
        print("Error: password cannot be empty.")
        return

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: passwords do not match.")
        return

    try:
        user = auth.create_user(conn, username, password, is_admin=True, participant_id=None)
        print(f"\nAdmin account '{user['username']}' created successfully.")
    except Exception as e:
        print(f"\nError creating user: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
