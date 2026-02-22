# Transfer Market Fantasy Game

A fantasy soccer game where participants draft teams of real players using their current Transfermarkt market values as prices.

## How it works

- Each participant starts with a fixed budget (default €100M).
- Players are added to the game by pasting a Transfermarkt profile URL — the app scrapes their name, club, position, and current value live.
- Once in the database, players can be bought and sold between participants.
- Selling a player returns their **current** market value to your budget.
- Roster cap: 15 players per participant.
- Run the scraper job periodically to refresh player values.

---

## Setup

### 1. Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- [Docker](https://www.docker.com/) installed and running

### 2. Install dependencies

```bash
uv sync
```

### 3. Start the database

```bash
docker compose up -d
```

This starts a PostgreSQL container and automatically runs `schema.sql` on first
boot to create the tables. Data is stored in a Docker named volume
(`postgres_data`) so it survives container restarts and removals.

### 4. Configure environment

```bash
cp .env.example .env
# Default credentials match docker-compose.yml — no edits needed unless you changed them
```

### 5. Run the app

```bash
uv run streamlit run app.py
```

---

## Refreshing player values

Run this periodically (e.g. weekly) to update market values for all players in the database:

```bash
uv run python scraper.py
```

---

## File overview

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Runs PostgreSQL in Docker with a persistent volume |
| `schema.sql` | Database schema — auto-applied by Docker on first boot |
| `scraper.py` | Fetches player data from Transfermarkt |
| `db.py` | All database read/write functions |
| `app.py` | Streamlit UI |
| `.env` | Local environment variables (not committed) |
