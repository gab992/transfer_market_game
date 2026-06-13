---
name: run-app
description: Launch and drive the Transfer Market Game Streamlit app locally — start the Docker Postgres, run Streamlit, log in past the auth gate, and screenshot pages with headless Chrome via Playwright. Use when asked to run, preview, screenshot, or visually verify the app.
---

# Running the Transfer Market Game app

The app is a single-file Streamlit UI ([app.py](../../../app.py)) fully gated
behind a login form. It needs a reachable Postgres at startup (`get_conn()`
runs before the login page renders).

## 1. Database

Connection resolution: `st.secrets["DATABASE_URL"]` if a secrets.toml exists,
else `DATABASE_URL` from `.env`. On this machine there is no
`~/.streamlit/secrets.toml`, so `.env` wins → local Docker Postgres at
`postgresql://postgres:postgres@localhost:5432/transfer_market_game`.

```bash
# Docker Desktop must be running (ask the user if `docker info` fails —
# do not launch Docker Desktop yourself)
docker compose up -d db
# wait until ready
until docker compose exec -T db pg_isready -U postgres >/dev/null 2>&1; do sleep 2; done
```

The named volume `postgres_data` persists between runs and already contains
test data (users, participants, players). `schema.sql` only auto-applies on a
fresh volume.

**Gotcha:** the local volume predates some migrations — `player_events` may be
missing, so the **Players page errors locally**. Screenshot Leaderboard, My
Team, Market, or Feed instead, or apply `migrations/*.sql` first.

## 2. Launch Streamlit

Use a non-default port so it can't clash with a session the user has open:

```bash
uv run streamlit run app.py --server.headless true --server.port 8511  # background
timeout 30 bash -c 'until curl -sf http://localhost:8511 >/dev/null; do sleep 1; done'
```

Stop afterwards with:

```bash
pkill -f "streamlit run app.py --server.headless true --server.port 8511"
```

## 3. Auth — create a throwaway login

Don't guess existing users' passwords. Create one, delete it when done:

```bash
uv run python - <<'EOF'
import psycopg2, psycopg2.extras, auth
conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/transfer_market_game",
                        cursor_factory=psycopg2.extras.RealDictCursor)
auth.create_user(conn, "ui_preview", "preview-pass-123", is_admin=False, participant_id=None)
conn.close()
EOF
```

`participant_id=None` is enough for Leaderboard/Feed. To see My Team/Market
with data, link a participant id not already taken by another user (UNIQUE
constraint). Pass `is_admin=True` to see the Admin page.

Cleanup:

```bash
docker compose exec -T db psql -U postgres -d transfer_market_game \
  -c "DELETE FROM users WHERE username = 'ui_preview';"
```

## 4. Screenshots — Playwright with system Chrome

`chromium-cli` is not installed here. Plain `chrome --headless --screenshot`
**does not work**: Streamlit renders over a websocket, so you only capture the
grey skeleton loader (`--virtual-time-budget` doesn't help). Use Playwright
with `channel="chrome"` — it drives the installed Google Chrome, no browser
download:

```bash
uv run --with playwright python - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto("http://localhost:8511")
    page.wait_for_selector("input", timeout=30000)          # login form
    page.fill('input[aria-label="Username"]', "ui_preview")
    page.fill('input[type="password"]', "preview-pass-123")
    page.get_by_text("Log in", exact=True).click()
    page.wait_for_selector("text=Leaderboard", timeout=30000)
    page.wait_for_timeout(3000)                              # let CSS/fonts settle
    page.screenshot(path="/tmp/tmg_app.png")

    # navigate via the sidebar radio, e.g.:
    # page.get_by_text("Feed", exact=True).click(); page.wait_for_timeout(3000)

    print("console errors:", errors[:5] if errors else "none")
    browser.close()
EOF
```

Read the screenshot file and **look at it** — then check the printed console
errors before declaring success.
