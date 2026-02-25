"""
fotmob_scraper.py — Fetches player market values from FotMob.

Two public functions:
  - scrape_fotmob_player(url)         : fetch a single player's market value by FotMob URL
  - refresh_all_fotmob_values()       : update fotmob_value for all players with a fotmob_url

FotMob is a Next.js app. Player pages embed their full data in a <script id="__NEXT_DATA__">
tag as JSON. We parse that rather than scraping raw HTML, which is more reliable.

Anti-bot strategy:
  - Full set of browser-like headers including Client Hints (Sec-Ch-Ua-*)
  - Random per-request delays (1–3 s) plus longer inter-player delays (15–45 s)
  - Single requests.Session reused across bulk calls to preserve cookies
  - Randomised User-Agent minor version to avoid fingerprinting
"""

import json
import re
import time
import random
import db
import requests
from bs4 import BeautifulSoup

# FotMob checks a broader set of headers than most sites because it's a modern
# React/Next.js app that serves an SPA shell — the browser sends full client
# hints on first load. We mirror those here.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.fotmob.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def scrape_fotmob_player(url: str, session: requests.Session = None) -> dict:
    """
    Fetch a player's market value from a FotMob player profile URL.

    Args:
        url: A FotMob player URL, e.g.:
             https://www.fotmob.com/players/418560/erling-haaland
        session: Optional requests.Session to reuse across calls (recommended
            for bulk scraping so cookies are maintained between requests).

    Returns:
        A dict with keys: name, current_value, fotmob_url
        current_value is an integer in euros (e.g. 180000000 for €180M).

    Raises:
        ValueError: if the page can't be parsed or the market value can't be found.
        requests.HTTPError: if the HTTP request fails.
    """
    url = _normalize_url(url)

    time.sleep(random.uniform(1, 3))

    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)

    response = session.get(url, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    name = _parse_name(soup)
    current_value = _parse_value(soup)

    return {
        "name": name,
        "current_value": current_value,
        "fotmob_url": url,
    }


def refresh_all_fotmob_values(conn, delay_range=(15, 45), on_player_done=None) -> list[dict]:
    """
    Re-scrape the FotMob market value for every player in the database that
    has a fotmob_url set, and update their fotmob_value field.

    Players without a fotmob_url are skipped.

    Args:
        conn: An open psycopg2 database connection.
        delay_range: A (min, max) tuple of seconds to wait between requests.
            Randomised to avoid bot detection. Defaults to (15, 45).
        on_player_done: Optional callback called after each player attempt with
            (index: int, total: int, result: dict). Useful for progress UIs.

    Returns:
        A list of dicts summarising the result for each attempted player:
        {"name": ..., "old_value": ..., "new_value": ..., "success": bool, "error": ...}
    """
    all_players = db.get_all_players_with_fotmob(conn)
    # Only process players that have a FotMob URL
    players = [p for p in all_players if p.get("fotmob_url")]

    results = []
    total = len(players)

    # Reuse a single session so cookies are preserved across requests,
    # making traffic look more like a real browser session.
    shared_session = requests.Session()
    shared_session.headers.update(HEADERS)

    for i, player in enumerate(players):
        result = {"name": player["name"], "old_value": player.get("fotmob_value")}
        try:
            data = scrape_fotmob_player(player["fotmob_url"], session=shared_session)
            db.update_player_fotmob_value(conn, player["id"], data["current_value"])
            result["new_value"] = data["current_value"]
            result["success"] = True
            result["error"] = None
        except Exception as e:
            result["new_value"] = player.get("fotmob_value")
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
    Strip query strings, fragments, and trailing slashes from a FotMob player URL.
    Expected format: https://www.fotmob.com/players/{id}/{slug}
    """
    return url.split("?")[0].split("#")[0].rstrip("/")


def _extract_player_id(url: str) -> str:
    """
    Extract the numeric player ID from a FotMob player URL.
    e.g. https://www.fotmob.com/players/418560/erling-haaland -> '418560'
    """
    match = re.search(r"/players/(\d+)", url)
    if not match:
        raise ValueError(f"Could not extract player ID from FotMob URL: {url}")
    return match.group(1)


def _parse_name(soup: BeautifulSoup) -> str:
    """
    Extract the player's name from the page.

    Primary: <script id="__NEXT_DATA__"> JSON embedded in the page.
    Fallback: og:title meta tag.
    """
    # Try __NEXT_DATA__ JSON first (most reliable)
    name = _find_in_next_data(soup, "name")
    if name and isinstance(name, str):
        return name.strip()

    # Fallback: og:title (e.g. "Erling Haaland | FotMob")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].split("|")[0].split("-")[0].strip()

    raise ValueError("Could not parse player name from FotMob page.")


def _parse_value(soup: BeautifulSoup) -> int:
    """
    Extract the market value from the FotMob page and return it as integer euros.

    FotMob displays market values as strings like '€180M', '€45M', '€450K'.
    """
    # Primary: parse __NEXT_DATA__ JSON
    raw = _find_market_value_in_next_data(soup)
    if raw:
        return _parse_value_string(raw)

    # Fallback: look for a marketValue-like pattern in the raw page text
    # FotMob renders values in text content that contains "Market value"
    page_text = soup.get_text()
    match = re.search(r"Market value[:\s]*([€$£][\d.,]+\s*[MKBmkb]?)", page_text)
    if match:
        return _parse_value_string(match.group(1))

    raise ValueError("Could not find market value on FotMob page.")


def _find_in_next_data(soup: BeautifulSoup, field: str):
    """
    Load the __NEXT_DATA__ JSON blob from the page and do a depth-first
    search for the first occurrence of `field`.
    """
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return None
    return _deep_find(data, field)


def _find_market_value_in_next_data(soup: BeautifulSoup):
    """
    Search the __NEXT_DATA__ JSON for a market value string.
    Tries several known field names FotMob has used across versions.
    """
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return None

    # Try known field names in order of preference
    for field in ("marketValue", "market_value", "marketValueEur", "value"):
        result = _deep_find(data, field)
        if result and isinstance(result, str) and re.search(r"[€$£]", result):
            return result

    return None


def _deep_find(obj, key: str):
    """
    Recursively search a nested dict/list for the first value under `key`.
    Returns None if not found.
    """
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _deep_find(v, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _deep_find(item, key)
            if result is not None:
                return result
    return None


def _parse_value_string(raw: str) -> int:
    """
    Convert a FotMob (or Transfermarkt-style) value string to integer euros.

    Examples:
        '€180M'   -> 180000000
        '€45M'    -> 45000000
        '€450K'   -> 450000
        '€1.2B'   -> 1200000000
        '€180.00m' -> 180000000  (TM-style also accepted)
        '€450Th.' -> 450000      (TM-style also accepted)
    """
    raw = raw.replace("\xa0", "").replace(",", "").strip()

    match = re.search(r"[€$£]([\d.]+)\s*(M|K|B|m|k|bn|Th\.)?", raw, re.I)
    if not match:
        raise ValueError(f"Could not parse FotMob value string: '{raw}'")

    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()

    if suffix in ("m",):
        return int(number * 1_000_000)
    elif suffix in ("k", "th."):
        return int(number * 1_000)
    elif suffix in ("b", "bn"):
        return int(number * 1_000_000_000)
    else:
        return int(number)


# ---------------------------------------------------------------------------
# CLI entry point — run this file directly to refresh all FotMob values
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import psycopg2
    from dotenv import load_dotenv
    import os

    load_dotenv()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])

    def log_progress(idx, total, result):
        status = "OK" if result["success"] else f"FAILED ({result['error']})"
        old = f"€{result['old_value']:,}" if result["old_value"] else "N/A"
        new = f"€{result['new_value']:,}" if result["new_value"] else "N/A"
        print(f"  [{idx}/{total}] {result['name']}: {old} -> {new}  [{status}]")

    print("Refreshing all FotMob player values...\n")
    results = refresh_all_fotmob_values(conn, on_player_done=log_progress)

    conn.close()
    successes = sum(1 for r in results if r["success"])
    print(f"\nDone. {successes}/{len(results)} updated successfully.")
