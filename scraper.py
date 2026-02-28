"""
scraper.py — Fetches player data from Transfermarkt or the Kaggle dataset.

Two public functions:
  - scrape_player(url)           : fetch a single player's details by URL
  - refresh_all_player_values()  : update current_value for all players in the DB

DATA_SOURCE controls which backend is used:
  - "kaggle"        : uses the dcaribou/transfermarkt-datasets Kaggle dataset (default).
                      Reliable, no bot detection issues. Dataset updates weekly.
  - "transfermarkt" : scrapes Transfermarkt directly. May return 403 on cloud IPs
                      due to Cloudflare bot detection. Works best from a local machine.

The active source can be changed at runtime via set_data_source(), or set via the
DATA_SOURCE environment variable before startup.
"""

import os
import re
import time
import random
from pathlib import Path

import db
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Data source selection
# ---------------------------------------------------------------------------

DATA_SOURCE = os.environ.get("DATA_SOURCE", "kaggle")  # "kaggle" | "transfermarkt"


def set_data_source(source: str) -> None:
    """Switch the active data acquisition backend at runtime."""
    global DATA_SOURCE
    if source not in ("kaggle", "transfermarkt"):
        raise ValueError(f"Unknown data source: {source!r}. Must be 'kaggle' or 'transfermarkt'.")
    DATA_SOURCE = source


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_player(url: str, session: requests.Session = None) -> dict:
    """
    Fetch a player's name, club, position, and current market value from
    a Transfermarkt profile URL.

    Uses DATA_SOURCE to decide whether to query the Kaggle dataset or
    scrape Transfermarkt directly.

    Args:
        url: A Transfermarkt player profile URL, e.g.:
             https://www.transfermarkt.com/erling-haaland/profil/spieler/418560
        session: Only used when DATA_SOURCE == "transfermarkt". Optional
            requests.Session to reuse across calls.

    Returns:
        A dict with keys: name, club, position, current_value, transfermrkt_url
        current_value is an integer in euros (e.g. 180000000 for €180M).

    Raises:
        ValueError: if the player can't be found or the page can't be parsed.
        requests.HTTPError: if a live scrape fails (transfermarkt source only).
    """
    if DATA_SOURCE == "kaggle":
        return _lookup_player_kaggle(url)
    return _scrape_player_transfermarkt(url, session=session)


def refresh_all_player_values(conn, delay_range=(15, 45), on_player_done=None) -> list[dict]:
    """
    Update the current market value for every player in the database.

    Uses DATA_SOURCE to decide whether to query the Kaggle dataset or
    scrape Transfermarkt directly.

    Args:
        conn: An open psycopg2 database connection.
        delay_range: Only used when DATA_SOURCE == "transfermarkt". A (min, max)
            tuple of seconds to wait between requests. Defaults to (15, 45).
        on_player_done: Optional callback called after each player with
            (index: int, total: int, result: dict). Useful for progress UIs.

    Returns:
        A list of dicts summarising the result for each player:
        {"name": ..., "old_value": ..., "new_value": ..., "success": bool, "error": ...}
    """
    if DATA_SOURCE == "kaggle":
        return _refresh_via_kaggle(conn, on_player_done=on_player_done)
    return _refresh_via_transfermarkt(conn, delay_range=delay_range, on_player_done=on_player_done)


# ---------------------------------------------------------------------------
# Kaggle dataset backend
# ---------------------------------------------------------------------------

_KAGGLE_DATASET = "davidcariboo/player-scores"
_CACHE_DIR = Path(__file__).parent / "data" / "transfermarkt_cache"
_CACHE_MAX_AGE_DAYS = 7
_CACHE_MARKER = _CACHE_DIR / ".last_updated"


def _get_player_id(url: str) -> int:
    """Extract the numeric Transfermarkt player ID from a profile URL."""
    match = re.search(r"/spieler/(\d+)", url)
    if not match:
        raise ValueError(
            f"Could not extract player ID from URL: {url!r}. "
            "Expected a URL containing '/spieler/<id>'."
        )
    return int(match.group(1))


def _ensure_cache_fresh(force: bool = False) -> None:
    """
    Download the Kaggle dataset if the local cache is missing, expired, or
    force=True. Files are saved to _CACHE_DIR.

    Requires KAGGLE_USERNAME and KAGGLE_KEY environment variables (or a
    ~/.kaggle/kaggle.json credential file).
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not force and _CACHE_MARKER.exists():
        age_days = (time.time() - _CACHE_MARKER.stat().st_mtime) / 86400
        if age_days < _CACHE_MAX_AGE_DAYS:
            return  # Cache is still fresh

    import kaggle  # imported here so the rest of the module works without it
    kaggle.api.authenticate()
    kaggle.api.dataset_download_files(_KAGGLE_DATASET, path=str(_CACHE_DIR), unzip=True, quiet=False)
    _CACHE_MARKER.touch()


def _load_dataframes():
    """Load the three CSVs we need from the cache and return (players_df, valuations_df, clubs_df)."""
    import pandas as pd

    players_df = pd.read_csv(_CACHE_DIR / "players.csv")
    valuations_df = pd.read_csv(_CACHE_DIR / "player_valuations.csv")
    clubs_df = pd.read_csv(_CACHE_DIR / "clubs.csv")
    return players_df, valuations_df, clubs_df


def _lookup_player_kaggle(url: str) -> dict:
    """Look up a single player's details from the cached Kaggle dataset."""
    import pandas as pd

    url = _normalize_url(url)
    player_id = _get_player_id(url)

    _ensure_cache_fresh()
    players_df, valuations_df, clubs_df = _load_dataframes()

    player_rows = players_df[players_df["player_id"] == player_id]
    if player_rows.empty:
        raise ValueError(
            f"Player ID {player_id} not found in the Kaggle dataset. "
            "They may be too new to appear — try switching to the Transfermarkt source."
        )
    player = player_rows.iloc[0]

    # Most recent valuation
    player_vals = valuations_df[valuations_df["player_id"] == player_id].sort_values("date", ascending=False)
    if player_vals.empty:
        raise ValueError(f"No market value found in dataset for player ID {player_id}.")
    current_value = int(player_vals.iloc[0]["market_value_in_eur"])

    # Club name via join
    club = "Unknown"
    club_id = player.get("current_club_id")
    if club_id and not pd.isna(club_id):
        club_rows = clubs_df[clubs_df["club_id"] == int(club_id)]
        if not club_rows.empty:
            club = club_rows.iloc[0]["name"]

    position = player.get("position", "Unknown")
    if not isinstance(position, str):
        position = "Unknown"

    return {
        "name": player["name"],
        "club": club,
        "position": position,
        "current_value": current_value,
        "transfermrkt_url": url,
    }


def _refresh_via_kaggle(conn, on_player_done=None) -> list[dict]:
    """Refresh all player values using the Kaggle dataset (force-downloads fresh data)."""
    # Always pull a fresh copy during a bulk refresh
    _ensure_cache_fresh(force=True)
    import pandas as pd
    valuations_df = pd.read_csv(_CACHE_DIR / "player_valuations.csv")

    db_players = db.get_all_players(conn)
    results = []
    total = len(db_players)

    for i, player in enumerate(db_players):
        result = {"name": player["name"], "old_value": player["current_value"]}
        try:
            player_id = _get_player_id(player["transfermrkt_url"])
            player_vals = (
                valuations_df[valuations_df["player_id"] == player_id]
                .sort_values("date", ascending=False)
            )
            if player_vals.empty:
                raise ValueError("No valuations found in dataset.")
            new_value = int(player_vals.iloc[0]["market_value_in_eur"])
            db.update_player_value(conn, player["id"], new_value, source="kaggle")
            result["new_value"] = new_value
            result["success"] = True
            result["error"] = None
        except Exception as e:
            result["new_value"] = player["current_value"]
            result["success"] = False
            result["error"] = str(e)

        results.append(result)
        if on_player_done:
            on_player_done(i + 1, total, result)

    return results


# ---------------------------------------------------------------------------
# Transfermarkt scraping backend (kept for direct/local use)
# ---------------------------------------------------------------------------

# Transfermarkt returns 403 without a full set of browser-like headers.
# A bare User-Agent is no longer sufficient — they also check Accept,
# Accept-Language, and other headers to distinguish bots from real browsers.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.transfermarkt.us/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


def _scrape_player_transfermarkt(url: str, session: requests.Session = None) -> dict:
    url = _normalize_url(url)

    time.sleep(random.uniform(1, 3))

    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
    response = session.get(url, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    return {
        "name": _parse_name(soup),
        "club": _parse_club(soup),
        "position": _parse_position(soup),
        "current_value": _parse_value(soup),
        "transfermrkt_url": url,
    }


def _refresh_via_transfermarkt(conn, delay_range=(15, 45), on_player_done=None) -> list[dict]:
    db_players = db.get_all_players(conn)
    results = []
    total = len(db_players)

    # Reuse a single session across all requests so cookies are preserved,
    # which makes the traffic look more like a real browser session.
    shared_session = requests.Session()
    shared_session.headers.update(HEADERS)

    for i, player in enumerate(db_players):
        result = {"name": player["name"], "old_value": player["current_value"]}
        try:
            data = _scrape_player_transfermarkt(player["transfermrkt_url"], session=shared_session)
            db.update_player_value(conn, player["id"], data["current_value"], source="transfermarkt")
            result["new_value"] = data["current_value"]
            result["success"] = True
            result["error"] = None
        except Exception as e:
            result["new_value"] = player["current_value"]
            result["success"] = False
            result["error"] = str(e)

        results.append(result)

        if on_player_done:
            on_player_done(i + 1, total, result)

        # Randomised delay between requests to reduce bot-detection risk.
        # Skip the delay after the last player.
        if i < total - 1:
            time.sleep(random.uniform(*delay_range))

    return results


# ---------------------------------------------------------------------------
# Private helpers (shared)
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """
    Ensure the URL points to the /profil/ page, which contains market value.
    Transfermarkt URLs sometimes link to /leistungsdaten/ or other sub-pages.
    """
    # Strip query string and fragment
    url = url.split("?")[0].split("#")[0].rstrip("/")

    # Replace any sub-page (e.g. /leistungsdaten/, /transfers/) with /profil/
    url = re.sub(r"/(leistungsdaten|transfers|news|stats|achievements)(/.*)?$", "", url)
    if "/profil/" not in url:
        # Append /profil/ if the path ends at the player slug/ID
        url = re.sub(r"(/spieler/\d+).*$", r"\1", url)
        if "/profil/" not in url:
            url = url + "/profil" if not url.endswith("/profil") else url

    return url


def _parse_name(soup: BeautifulSoup) -> str:
    """Extract the player's full name from the page."""
    tag = soup.find("h1", class_=re.compile(r"data-header__headline"))
    if tag:
        # The headline contains the name, sometimes with a span for shirt number
        for span in tag.find_all("span"):
            span.decompose()
        return tag.get_text(strip=True)

    # Fallback: og:title meta tag (e.g. "Erling Haaland - Man City - Profile")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].split(" - ")[0].strip()

    raise ValueError("Could not parse player name from page.")


def _parse_club(soup: BeautifulSoup) -> str:
    """Extract the player's current club."""
    # The club appears in the data-header as a link inside the header details
    tag = soup.find("span", string=re.compile(r"Current club", re.I))
    if tag:
        club_tag = tag.find_next("a")
        if club_tag:
            return club_tag.get_text(strip=True)

    # Fallback: look for it in the main data header info box
    header = soup.find("div", class_=re.compile(r"data-header__club"))
    if header:
        return header.get_text(strip=True)

    return "Unknown"


def _parse_position(soup: BeautifulSoup) -> str:
    """Extract the player's position."""
    tag = soup.find("span", string=re.compile(r"Position", re.I))
    if tag:
        value_tag = tag.find_next("span", class_=re.compile(r"data-header__label"))
        if value_tag:
            return value_tag.get_text(strip=True)

    # Fallback: look inside the info table on the profile page
    label = soup.find("span", string=re.compile(r"^Position$", re.I))
    if label:
        sibling = label.find_next_sibling()
        if sibling:
            return sibling.get_text(strip=True)

    return "Unknown"


def _parse_value(soup: BeautifulSoup) -> int:
    """
    Extract the current market value and convert it to an integer in euros.

    Transfermarkt displays values like '€180.00m' or '€450Th.'.
    """
    tag = soup.find("a", class_=re.compile(r"data-header__market-value"))
    if not tag:
        # Try an alternative selector used on some page layouts
        tag = soup.find("div", class_=re.compile(r"tm-player-market-value"))

    if not tag:
        raise ValueError("Could not find market value element on the page.")

    raw = tag.get_text(strip=True)
    return _parse_value_string(raw)


def _parse_value_string(raw: str) -> int:
    """
    Convert a Transfermarkt value string to an integer number of euros.

    Examples:
        '€180.00m'  -> 180000000
        '€450Th.'   -> 450000
        '€1.20bn'   -> 1200000000  (hypothetical)
    """
    raw = raw.replace("\xa0", "").replace(",", ".").strip()

    # Extract numeric part and suffix
    match = re.search(r"€([\d.]+)\s*(m|Th\.|bn|k)?", raw, re.I)
    if not match:
        raise ValueError(f"Could not parse value string: '{raw}'")

    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()

    if suffix == "m":
        return int(number * 1_000_000)
    elif suffix in ("th.", "k"):
        return int(number * 1_000)
    elif suffix == "bn":
        return int(number * 1_000_000_000)
    else:
        return int(number)


# ---------------------------------------------------------------------------
# CLI entry point — run this file directly to refresh all player values
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])

    def log_progress(idx, total, result):
        status = "OK" if result["success"] else f"FAILED ({result['error']})"
        old = f"€{result['old_value']:,}"
        new = f"€{result['new_value']:,}"
        print(f"  [{idx}/{total}] {result['name']}: {old} -> {new}  [{status}]")

    print(f"Refreshing all player values via '{DATA_SOURCE}'...\n")
    results = refresh_all_player_values(conn, on_player_done=log_progress)

    conn.close()
    successes = sum(1 for r in results if r["success"])
    print(f"\nDone. {successes}/{len(results)} updated successfully.")
