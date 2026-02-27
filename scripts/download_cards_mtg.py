#!/usr/bin/python3
"""
Download Magic: The Gathering card images and metadata from Scryfall.
Uses the per-set search API for low memory usage (works on Pi Zero with 512MB RAM).
Supports resume - re-run safely to pick up where you left off.

Usage:
    python3 download_cards_mtg.py              # Download all MTG cards
    python3 download_cards_mtg.py --since 2018 # Only sets released 2018 or later
"""

import os
import requests
import json
import time
import random
import sys
import argparse
import gc

# --- CONFIGURATION ---
BASE_DIR = "/home/pi/mtg_cards"

# Scryfall API (free, no API key needed)
SETS_URL = "https://api.scryfall.com/sets"
SEARCH_URL = "https://api.scryfall.com/cards/search"

HEADERS = {
    'User-Agent': 'InkSlab/1.0 (https://github.com/costamesatechsolutions/inkslab-eink-tcg-display)',
    'Accept': 'application/json',
}

# Scryfall asks for 50-100ms between API requests
API_DELAY = 0.1  # seconds between API calls

# Rate limiting for image CDN (be polite)
DOWNLOAD_DELAY_MIN = 0.1  # seconds
DOWNLOAD_DELAY_MAX = 0.3
COOLDOWN_EVERY = 200      # downloads
COOLDOWN_SECONDS = 10

# Skip these layout types (no standard card image)
SKIP_LAYOUTS = {"art_series", "token", "double_faced_token", "emblem", "planar", "scheme", "vanguard"}

# Set types that contain real playable cards with standard card images
INCLUDE_SET_TYPES = {
    "core", "expansion", "masters", "draft_innovation",
    "commander", "starter", "duel_deck", "planechase",
    "archenemy", "premium_deck", "from_the_vault",
    "spellbook", "arsenal", "funny", "masterpiece", "box",
}


def download_file(url, filepath):
    """Download a file, skipping if it already exists."""
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return "EXISTS"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(r.content)
            return "DOWNLOADED"
        return f"HTTP {r.status_code}"
    except Exception as e:
        return f"FAIL: {e}"


def fetch_sets(since_year=None):
    """Fetch all MTG sets from Scryfall, filtered by type and optional year."""
    print("1. Fetching set list from Scryfall...")
    try:
        r = requests.get(SETS_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        all_sets = r.json().get("data", [])
    except Exception as e:
        print(f"   Error fetching sets: {e}")
        return []

    today = time.strftime("%Y-%m-%d")
    filtered = []
    for s in all_sets:
        set_type = s.get("set_type", "")
        if set_type not in INCLUDE_SET_TYPES:
            continue
        released = s.get("released_at", "9999-99-99")
        # Skip sets with future release dates
        if released > today:
            continue
        if since_year and released[:4] < str(since_year):
            continue
        filtered.append(s)

    # Sort by release date, newest first
    filtered.sort(key=lambda s: s.get("released_at", "0000"), reverse=True)
    total_cards = sum(s.get("card_count", 0) for s in filtered)
    print(f"   Found {len(filtered)} sets (~{total_cards} cards)")
    return filtered


def fetch_cards_for_set(set_code):
    """Fetch all English cards for a single set using paginated search API.
    Returns a list of card dicts. Memory-friendly: only one page (~175 cards) at a time."""
    cards = []
    url = f"{SEARCH_URL}?q=set%3A{set_code}+lang%3Aen&unique=prints&order=set"

    while url:
        time.sleep(API_DELAY)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                # No cards found for this set (empty set)
                break
            r.raise_for_status()
            page = r.json()
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 429:
                # Rate limited -- back off and retry
                print("     Rate limited, waiting 2s...")
                time.sleep(2)
                continue
            print(f"     API error for set {set_code}: {e}")
            break
        except Exception as e:
            print(f"     Request error for set {set_code}: {e}")
            break

        page_cards = page.get("data", [])
        # Filter: paper cards, standard layouts, with images
        for card in page_cards:
            if "paper" not in card.get("games", []):
                continue
            if card.get("layout") in SKIP_LAYOUTS:
                continue
            if "image_uris" not in card:
                continue
            cards.append(card)

        if page.get("has_more") and page.get("next_page"):
            url = page["next_page"]
        else:
            url = None

    return cards


def process_set(set_info, cards):
    """Build _data.json and download images for one set. Returns (new_downloads, skipped)."""
    set_code = set_info["code"]
    set_dir = os.path.join(BASE_DIR, set_code)
    os.makedirs(set_dir, exist_ok=True)

    # Build per-set _data.json
    slim_db = {}
    for card in cards:
        card_id = card["id"]
        slim_db[card_id] = {
            "name": card.get("name", "Unknown"),
            "number": card.get("collector_number", "00"),
            "rarity": card.get("rarity", "common").replace("_", " ").title(),
        }

    data_file = os.path.join(set_dir, "_data.json")
    with open(data_file, "w") as f:
        json.dump(slim_db, f)

    # Download images
    download_count = 0
    skip_count = 0
    for card in cards:
        card_id = card["id"]
        img_url = card.get("image_uris", {}).get("large",
                  card.get("image_uris", {}).get("normal"))
        if not img_url:
            continue

        # Use Scryfall UUID as filename (unique across all sets)
        filepath = os.path.join(set_dir, f"{card_id}.png")
        status = download_file(img_url, filepath)

        if status == "DOWNLOADED":
            download_count += 1
            time.sleep(random.uniform(DOWNLOAD_DELAY_MIN, DOWNLOAD_DELAY_MAX))
            if download_count % COOLDOWN_EVERY == 0:
                print(f"     [Cooldown {COOLDOWN_SECONDS}s...]")
                time.sleep(COOLDOWN_SECONDS)
        elif status == "EXISTS":
            skip_count += 1
        else:
            print(f"     Failed: {card.get('name', card_id)} ({status})")

    return download_count, skip_count


def main():
    parser = argparse.ArgumentParser(description="Download MTG card images from Scryfall")
    parser.add_argument("--since", type=int, metavar="YEAR",
                        help="Only download sets released in this year or later (e.g. --since 2018)")
    args = parser.parse_args()

    print("=== MTG Card Downloader (Scryfall) ===\n")

    if args.since:
        print(f"Filtering to sets released {args.since} or later.\n")

    sets = fetch_sets(since_year=args.since)
    if not sets:
        print("No sets found to download.")
        return

    os.makedirs(BASE_DIR, exist_ok=True)

    # Build master_index.json from set list (no card data needed)
    master_index = {}
    for s in sets:
        master_index[s["code"]] = {
            "name": s["name"],
            "year": s.get("released_at", "0000")[:4],
        }
    index_path = os.path.join(BASE_DIR, "master_index.json")
    with open(index_path, "w") as f:
        json.dump(master_index, f)
    print(f"2. Saved master_index.json ({len(master_index)} sets)\n")

    print("3. Downloading cards per set...")
    print("   Press CTRL+C to stop (you can resume later).\n")

    total_downloaded = 0
    total_skipped = 0

    for i, s in enumerate(sets):
        set_code = s["code"]
        set_name = s["name"]
        expected = s.get("card_count", "?")
        print(f"[{i + 1}/{len(sets)}] {set_name} ({set_code}) — ~{expected} cards")

        cards = fetch_cards_for_set(set_code)
        if not cards:
            print(f"     No downloadable cards found, skipping.")
            continue

        print(f"     Fetched {len(cards)} cards from API, downloading images...")
        new_downloads, skipped = process_set(s, cards)
        total_downloaded += new_downloads
        total_skipped += skipped

        if new_downloads > 0:
            print(f"     +{new_downloads} new, {skipped} already existed")
        elif skipped > 0:
            print(f"     All {skipped} cards already downloaded")

        # Free memory between sets
        del cards
        gc.collect()

    print(f"\n=== Done! Downloaded {total_downloaded} new images, "
          f"skipped {total_skipped} existing. ===")


if __name__ == "__main__":
    main()
