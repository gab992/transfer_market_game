"""
scraper.py — Fetches player data from Transfermarkt.

Two public functions:
  - scrape_player(url)           : fetch a single player's details by URL
  - refresh_all_player_values()  : update current_value for all players in the DB

Transfermarkt blocks generic Python user-agent strings, so we spoof a browser
User-Agent header on every request.
"""

import re
import time
import random
import db
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# Transfermarkt returns 403 without a real-looking User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def scrape_player(url: str) -> dict:
    """
    Fetch a player's name, club, position, and current market value from
    a Transfermarkt profile URL.

    Args:
        url: A Transfermarkt player profile URL, e.g.:
             https://www.transfermarkt.com/erling-haaland/profil/spieler/418560

    Returns:
        A dict with keys: name, club, position, current_value, transfermrkt_url
        current_value is an integer in euros (e.g. 180000000 for €180M).

    Raises:
        ValueError: if the page can't be parsed or the value can't be found.
        requests.HTTPError: if the HTTP request fails.
    """
    # Normalize the URL: strip query strings and ensure it points to /profil/
    # Transfermarkt player URLs can come in several formats; the profile page
    # has the market value we need.
    url = _normalize_url(url)

    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    name = _parse_name(soup)
    club = _parse_club(soup)
    position = _parse_position(soup)
    current_value = _parse_value(soup)

    return {
        "name": name,
        "club": club,
        "position": position,
        "current_value": current_value,
        "transfermrkt_url": url,
    }


def refresh_all_player_values(conn, delay_range=(15, 45), on_player_done=None) -> list[dict]:
    """
    Re-scrape the current market value for every player in the database
    and update their current_value and last_updated fields.

    Intended to be run periodically (e.g. weekly) to keep values fresh.

    Args:
        conn: An open psycopg2 database connection.
        delay_range: A (min, max) tuple of seconds to wait between requests.
            Randomised to avoid bot detection. Defaults to (15, 45).
        on_player_done: Optional callback called after each player with
            (index: int, total: int, result: dict). Useful for progress UIs.

    Returns:
        A list of dicts summarising the result for each player:
        {"name": ..., "old_value": ..., "new_value": ..., "success": bool, "error": ...}
    """
    players = db.get_all_players(conn)
    results = []
    total = len(players)

    for i, player in enumerate(players):
        result = {"name": player["name"], "old_value": player["current_value"]}
        try:
            data = scrape_player(player["transfermrkt_url"])
            db.update_player_value(conn, player["id"], data["current_value"])
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
# Private helpers
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
    match = re.search(r"€([\d.]+)\s*(m|Th\.|bn)?", raw, re.I)
    if not match:
        raise ValueError(f"Could not parse value string: '{raw}'")

    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()

    if suffix == "m":
        return int(number * 1_000_000)
    elif suffix == "th.":
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
    import os

    load_dotenv()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])

    def log_progress(idx, total, result):
        status = "OK" if result["success"] else f"FAILED ({result['error']})"
        old = f"€{result['old_value']:,}"
        new = f"€{result['new_value']:,}"
        print(f"  [{idx}/{total}] {result['name']}: {old} -> {new}  [{status}]")

    print("Refreshing all player values...\n")
    results = refresh_all_player_values(conn, on_player_done=log_progress)

    conn.close()
    successes = sum(1 for r in results if r["success"])
    print(f"\nDone. {successes}/{len(results)} updated successfully.")
