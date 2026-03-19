#!/usr/bin/python3
"""
Download all Pokemon card images and metadata from the PokemonTCG GitHub data repository.
Supports resume - re-run safely to pick up where you left off.
"""

import os
import tempfile
import requests
import json
import shutil
import time
import random
import sys


def _atomic_json_write(path, data):
    """Write JSON via temp+rename so power loss can't corrupt the file."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

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


MIN_FREE_SPACE_MB = 50


def check_disk_space():
    """Return True if there's enough free space to continue downloading."""
    try:
        st = shutil.disk_usage(BASE_DIR)
        return (st.free // (1024 * 1024)) >= MIN_FREE_SPACE_MB
    except Exception:
        return True  # Don't block on check failure


def download_file(url, filepath):
    """Download a file, skipping if it already exists. Writes to temp file first."""
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return "EXISTS"
    tmp = filepath + ".tmp"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            with open(tmp, 'wb') as f:
                f.write(r.content)
            if os.path.getsize(tmp) > 0:
                os.rename(tmp, filepath)
                return "DOWNLOADED"
            os.remove(tmp)
            return "FAIL: empty response"
        return f"HTTP {r.status_code}"
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return f"FAIL: {e}"


def main():
    os.makedirs(BASE_DIR, exist_ok=True)

    print("=== Pokemon Card Downloader ===")
    print("1. Fetching master set list...")

    try:
        r = requests.get(SETS_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        sets = r.json()
    except Exception as e:
        print(f"Error fetching sets: {e}")
        return

    # Build master_index.json
    master_index = {}
    for s in sets:
        master_index[s['id']] = {
            "name": s['name'],
            "year": s['releaseDate'][:4]
        }

    index_path = os.path.join(BASE_DIR, "master_index.json")
    _atomic_json_write(index_path, master_index)
    print(f"   Saved master_index.json ({len(master_index)} sets)")

    # Start with newest sets
    sets.reverse()

    total_sets = len(sets)
    download_count = 0

    print(f"\n2. Downloading cards from {total_sets} sets...")
    print("   Press CTRL+C to stop (you can resume later).\n")

    for i, s in enumerate(sets):
        set_id = s['id']
        set_name = s['name']
        set_dir = os.path.join(BASE_DIR, set_id)
        os.makedirs(set_dir, exist_ok=True)

        print(f"[{i + 1}/{total_sets}] {set_name}...")

        try:
            r = requests.get(f"{CARDS_BASE_URL}{set_id}.json", headers=HEADERS, timeout=30)
            r.raise_for_status()
            cards = r.json()
        except Exception:
            print(f"  > Error fetching card list. Skipping.")
            continue

        # Build _data.json for this set
        slim_db = {}
        for card in cards:
            c_id = card['id']
            slim_db[c_id] = {
                "name": card.get('name', 'Unknown'),
                "number": card.get('number', '00'),
                "rarity": card.get('rarity', 'Common'),
            }

        data_file = os.path.join(set_dir, "_data.json")
        _atomic_json_write(data_file, slim_db)

        # Download card images
        for card in cards:
            card_id = card['id']
            if 'images' not in card:
                continue

            img_url = card['images'].get('large', card['images'].get('small'))
            if not img_url:
                continue

            if not check_disk_space():
                print(f"\n=== STOPPING: Less than {MIN_FREE_SPACE_MB}MB free space remaining. ===")
                print(f"=== Downloaded {download_count} new cards before stopping. ===")
                return

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
