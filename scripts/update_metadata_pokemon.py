#!/usr/bin/python3
"""
Update card metadata (names, numbers, rarities) for all downloaded sets.
Creates/updates _data.json in each set folder and master_index.json in the library root.
"""

import os
import requests
import json
import time

# --- CONFIGURATION ---
LIBRARY_DIR = "/home/pi/pokemon_cards"

# Data sources
SETS_URL = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/sets/en.json"
CARDS_BASE_URL = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/cards/en/"


def update_master_index():
    """Download and save the master set index (set IDs -> names + years)."""
    print("Updating master index...")
    try:
        data = requests.get(SETS_URL).json()
        master_index = {}
        for s in data:
            master_index[s['id']] = {
                "name": s['name'],
                "year": s['releaseDate'][:4]
            }

        index_path = os.path.join(LIBRARY_DIR, "master_index.json")
        with open(index_path, 'w') as f:
            json.dump(master_index, f)

        print(f"  > Saved {len(master_index)} sets to master_index.json")
        return master_index
    except Exception as e:
        print(f"  > Error: {e}")
        return {}


def update_card_data():
    """Update _data.json for each set with names, numbers, and rarities."""
    print("\nUpdating card metadata...")

    sets = sorted(d for d in os.listdir(LIBRARY_DIR) if os.path.isdir(os.path.join(LIBRARY_DIR, d)))

    for set_id in sets:
        print(f"  {set_id}...", end=" ")
        set_path = os.path.join(LIBRARY_DIR, set_id)
        data_file = os.path.join(set_path, "_data.json")

        try:
            res = requests.get(f"{CARDS_BASE_URL}{set_id}.json")
            if res.status_code != 200:
                print("not found on server")
                continue

            full_data = res.json()
            slim_db = {}
            for card in full_data:
                c_id = card['id']
                slim_db[c_id] = {
                    "name": card.get('name', 'Unknown'),
                    "number": card.get('number', '00'),
                    "rarity": card.get('rarity', 'Common'),
                }

            with open(data_file, 'w') as f:
                json.dump(slim_db, f)
            print(f"{len(slim_db)} cards")

        except Exception as e:
            print(f"error: {e}")

        time.sleep(0.2)


def main():
    print("=== Pokemon Card Metadata Updater ===\n")

    if not os.path.exists(LIBRARY_DIR):
        print(f"Error: {LIBRARY_DIR} not found. Download cards first.")
        return

    update_master_index()
    update_card_data()

    print("\n=== Done! ===")


if __name__ == "__main__":
    main()
