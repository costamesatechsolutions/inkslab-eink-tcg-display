#!/usr/bin/python3
"""
Download Magic: The Gathering card images and metadata from Scryfall.
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
import tempfile

# --- CONFIGURATION ---
BASE_DIR = "/home/pi/mtg_cards"

# Scryfall API (free, no API key needed)
BULK_DATA_URL = "https://api.scryfall.com/bulk-data/default-cards"

HEADERS = {
    'User-Agent': 'InkSlab/1.0 (https://github.com/costamesatechsolutions/inkslab-eink-tcg-display)',
    'Accept': 'application/json',
}

# Rate limiting (Scryfall image CDN has no rate limit, but be polite)
DOWNLOAD_DELAY_MIN = 0.1  # seconds
DOWNLOAD_DELAY_MAX = 0.3
COOLDOWN_EVERY = 200      # downloads
COOLDOWN_SECONDS = 10

# Skip these layout types (no standard card image)
SKIP_LAYOUTS = {"art_series", "token", "double_faced_token", "emblem", "planar", "scheme", "vanguard"}


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


def fetch_bulk_data(since_year=None):
    """Download and parse Scryfall bulk data. Returns list of filtered card dicts."""
    print("1. Fetching bulk data download URL...")
    try:
        r = requests.get(BULK_DATA_URL, headers=HEADERS, timeout=30)
        bulk_info = r.json()
        download_url = bulk_info['download_uri']
        size_mb = bulk_info.get('size', 0) / (1024 * 1024)
        print(f"   Bulk data: {size_mb:.0f} MB")
    except Exception as e:
        print(f"   Error fetching bulk data info: {e}")
        return []

    print("2. Downloading bulk data (this takes a few minutes)...")
    tmp_path = os.path.join(tempfile.gettempdir(), "scryfall_bulk.json")
    try:
        r = requests.get(download_url, headers=HEADERS, stream=True, timeout=600)
        downloaded = 0
        with open(tmp_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                print(f"\r   Downloaded {downloaded / (1024*1024):.0f} MB...", end="", flush=True)
        print(f"\r   Downloaded {downloaded / (1024*1024):.0f} MB — done.")
    except Exception as e:
        print(f"\n   Error downloading bulk data: {e}")
        return []

    print("3. Parsing card data...")
    try:
        with open(tmp_path, 'r', encoding='utf-8') as f:
            all_cards = json.load(f)
    except Exception as e:
        print(f"   Error parsing JSON: {e}")
        return []

    # Filter: English, paper game, standard layouts, with images
    filtered = []
    for card in all_cards:
        if card.get('lang') != 'en':
            continue
        if 'paper' not in card.get('games', []):
            continue
        if card.get('layout') in SKIP_LAYOUTS:
            continue
        if 'image_uris' not in card:
            continue
        if since_year and card.get('released_at', '9999')[:4] < str(since_year):
            continue
        filtered.append(card)

    print(f"   {len(filtered)} cards after filtering ({len(all_cards)} total in bulk data)")

    # Clean up temp file
    try:
        os.remove(tmp_path)
    except OSError:
        pass

    return filtered


def build_metadata(cards):
    """Build master_index.json and per-set _data.json from card list."""
    print("4. Building metadata...")
    os.makedirs(BASE_DIR, exist_ok=True)

    # Group cards by set
    sets = {}
    for card in cards:
        set_code = card['set']
        if set_code not in sets:
            sets[set_code] = {
                "name": card['set_name'],
                "year": card.get('released_at', '0000')[:4],
                "cards": []
            }
        sets[set_code]["cards"].append(card)

    # Master index
    master_index = {}
    for set_code, info in sets.items():
        master_index[set_code] = {
            "name": info["name"],
            "year": info["year"]
        }

    index_path = os.path.join(BASE_DIR, "master_index.json")
    with open(index_path, 'w') as f:
        json.dump(master_index, f)
    print(f"   Saved master_index.json ({len(master_index)} sets)")

    # Per-set _data.json
    for set_code, info in sets.items():
        set_dir = os.path.join(BASE_DIR, set_code)
        os.makedirs(set_dir, exist_ok=True)

        slim_db = {}
        for card in info["cards"]:
            card_id = card['id']
            slim_db[card_id] = {
                "name": card.get('name', 'Unknown'),
                "number": card.get('collector_number', '00'),
                "rarity": card.get('rarity', 'common').replace('_', ' ').title(),
            }

        data_file = os.path.join(set_dir, "_data.json")
        with open(data_file, 'w') as f:
            json.dump(slim_db, f)

    print(f"   Saved _data.json for {len(sets)} sets")
    return sets


def download_images(sets):
    """Download card images from Scryfall CDN."""
    total_cards = sum(len(info["cards"]) for info in sets.values())
    print(f"\n5. Downloading card images ({total_cards} cards)...")
    print("   Press CTRL+C to stop (you can resume later).\n")

    download_count = 0
    skip_count = 0
    card_num = 0

    # Sort sets by year (newest first)
    sorted_sets = sorted(sets.items(), key=lambda x: x[1]["year"], reverse=True)

    for set_code, info in sorted_sets:
        set_dir = os.path.join(BASE_DIR, set_code)
        set_name = info["name"]
        print(f"   {set_name} ({set_code}) — {len(info['cards'])} cards")

        for card in info["cards"]:
            card_num += 1
            card_id = card['id']
            img_url = card['image_uris'].get('large', card['image_uris'].get('normal'))
            if not img_url:
                continue

            # Use Scryfall UUID as filename (unique across all sets)
            filepath = os.path.join(set_dir, f"{card_id}.png")
            status = download_file(img_url, filepath)

            if status == "DOWNLOADED":
                download_count += 1
                if download_count % 50 == 0:
                    print(f"     [{download_count} downloaded, {card_num}/{total_cards} processed]")

                time.sleep(random.uniform(DOWNLOAD_DELAY_MIN, DOWNLOAD_DELAY_MAX))
                if download_count % COOLDOWN_EVERY == 0:
                    print(f"     [Cooldown {COOLDOWN_SECONDS}s...]")
                    time.sleep(COOLDOWN_SECONDS)
            elif status == "EXISTS":
                skip_count += 1
            else:
                print(f"     Failed: {card.get('name', card_id)} ({status})")

    print(f"\n=== Done! Downloaded {download_count} new images, skipped {skip_count} existing. ===")


def main():
    parser = argparse.ArgumentParser(description="Download MTG card images from Scryfall")
    parser.add_argument('--since', type=int, metavar='YEAR',
                        help='Only download sets released in this year or later (e.g. --since 2018)')
    args = parser.parse_args()

    print("=== MTG Card Downloader (Scryfall) ===\n")

    if args.since:
        print(f"Filtering to sets released {args.since} or later.\n")

    cards = fetch_bulk_data(since_year=args.since)
    if not cards:
        print("No cards to download.")
        return

    sets = build_metadata(cards)
    download_images(sets)


if __name__ == "__main__":
    main()
