#!/usr/bin/python3
"""
Download all Pokemon card images from the PokemonTCG GitHub data repository.
Supports resume - re-run safely to pick up where you left off.
"""

import os
import requests
import json
import time
import random
import sys

# --- CONFIGURATION ---
BASE_DIR = "/home/pi/pokemon_cards"

# Data sources (PokemonTCG open data repo)
SETS_URL = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/sets/en.json"
CARDS_BASE_URL = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/cards/en/"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36'
}

# Rate limiting
DOWNLOAD_DELAY_MIN = 1.5  # seconds
DOWNLOAD_DELAY_MAX = 3.0
COOLDOWN_EVERY = 50       # downloads
COOLDOWN_SECONDS = 30


def download_file(url, filepath):
    """Download a file, skipping if it already exists."""
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return "EXISTS"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(r.content)
            return "DOWNLOADED"
        return f"HTTP {r.status_code}"
    except Exception as e:
        return f"FAIL: {e}"


def main():
    os.makedirs(BASE_DIR, exist_ok=True)

    print("=== Pokemon Card Downloader ===")
    print("1. Fetching master set list...")

    try:
        r = requests.get(SETS_URL, headers=HEADERS)
        sets = r.json()
    except Exception as e:
        print(f"Error fetching sets: {e}")
        return

    # Start with newest sets
    sets.reverse()

    total_sets = len(sets)
    download_count = 0

    print(f"Found {total_sets} sets. Starting download...")
    print("Press CTRL+C to stop (you can resume later).\n")

    for i, s in enumerate(sets):
        set_id = s['id']
        set_name = s['name']
        set_dir = os.path.join(BASE_DIR, set_id)
        os.makedirs(set_dir, exist_ok=True)

        print(f"[{i + 1}/{total_sets}] {set_name}...")

        try:
            r = requests.get(f"{CARDS_BASE_URL}{set_id}.json", headers=HEADERS)
            cards = r.json()
        except Exception:
            print(f"  > Error fetching card list. Skipping.")
            continue

        for card in cards:
            card_id = card['id']
            if 'images' not in card:
                continue

            img_url = card['images'].get('large', card['images'].get('small'))
            if not img_url:
                continue

            filepath = os.path.join(set_dir, f"{card_id}.png")
            status = download_file(img_url, filepath)

            if status == "DOWNLOADED":
                download_count += 1
                print(f"  > [{download_count}] {card.get('name', card_id)}")

                # Rate limiting
                time.sleep(random.uniform(DOWNLOAD_DELAY_MIN, DOWNLOAD_DELAY_MAX))
                if download_count % COOLDOWN_EVERY == 0:
                    print(f"    [Cooldown {COOLDOWN_SECONDS}s...]")
                    time.sleep(COOLDOWN_SECONDS)
            elif status != "EXISTS":
                print(f"  > Failed: {card_id} ({status})")

    print(f"\n=== Done! Downloaded {download_count} new cards. ===")


if __name__ == "__main__":
    main()
