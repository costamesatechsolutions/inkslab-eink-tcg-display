#!/usr/bin/python3
"""
Download Disney Lorcana card images and metadata from the Lorcast API.
Supports resume - re-run safely to pick up where you left off.

Usage:
    python3 download_cards_lorcana.py
"""

import os
import tempfile
import requests
import json
import shutil
import time
import random
import gc


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
BASE_DIR = "/home/pi/lorcana_cards"

# Lorcast API (free, no API key needed)
API_BASE = "https://api.lorcast.com/v0"

HEADERS = {
    'User-Agent': 'InkSlab/1.0 (https://github.com/costamesatechsolutions/inkslab-eink-tcg-display)',
    'Accept': 'application/json',
}

# Rate limiting (be polite to the free API)
API_DELAY = 0.15  # seconds between API calls
DOWNLOAD_DELAY_MIN = 0.1
DOWNLOAD_DELAY_MAX = 0.3
COOLDOWN_EVERY = 100
COOLDOWN_SECONDS = 10

MIN_FREE_SPACE_MB = 50


def check_disk_space():
    """Return True if there's enough free space to continue downloading."""
    try:
        st = shutil.disk_usage(BASE_DIR)
        return (st.free // (1024 * 1024)) >= MIN_FREE_SPACE_MB
    except Exception:
        return True


def download_file(url, filepath):
    """Download a file, skipping if it already exists. Writes to temp file first."""
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return "EXISTS"
    tmp = filepath + ".tmp"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if r.status_code == 200:
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
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


def fetch_sets():
    """Fetch all Lorcana sets from Lorcast API."""
    print("1. Fetching set list from Lorcast...")
    try:
        r = requests.get(f"{API_BASE}/sets", headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        # API returns {"results": [...]} or just [...]
        sets = data if isinstance(data, list) else data.get("results", data.get("data", []))
    except Exception as e:
        print(f"   Error fetching sets: {e}")
        return []

    print(f"   Found {len(sets)} sets")
    return sets


def fetch_cards_for_set(set_code):
    """Fetch all cards for a single set."""
    cards = []
    url = f"{API_BASE}/sets/{set_code}/cards"
    retries = 0
    max_retries = 5

    while url:
        time.sleep(API_DELAY)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                break
            r.raise_for_status()
            data = r.json()
            retries = 0
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 429:
                retries += 1
                if retries > max_retries:
                    print(f"     Rate limited {max_retries} times, skipping rest of set.")
                    break
                wait = 2 * retries
                print(f"     Rate limited, waiting {wait}s (retry {retries}/{max_retries})...")
                time.sleep(wait)
                continue
            print(f"     API error for set {set_code}: {e}")
            break
        except Exception as e:
            print(f"     Request error for set {set_code}: {e}")
            break

        # Handle paginated or flat response
        if isinstance(data, list):
            page_cards = data
            url = None
        else:
            page_cards = data.get("results", data.get("data", []))
            # Check for pagination
            next_url = data.get("next", None)
            url = next_url if next_url else None

        for card in page_cards:
            cards.append(card)

        # If response was a flat list, no pagination
        if isinstance(data, list):
            break

    return cards


def process_set(set_info, cards):
    """Build _data.json and download images for one set. Returns (new_downloads, skipped)."""
    set_code = set_info.get("code", set_info.get("id", "unknown"))
    set_dir = os.path.join(BASE_DIR, set_code)
    os.makedirs(set_dir, exist_ok=True)

    # Build per-set _data.json
    slim_db = {}
    for card in cards:
        # Use a stable card identifier
        card_id = card.get("id", "")
        if not card_id:
            continue
        # Normalize ID: use string version, replace problematic chars
        card_id = str(card_id)

        collector_num = card.get("collector_number", card.get("number", "00"))
        rarity = card.get("rarity", "Common")
        if isinstance(rarity, str):
            rarity = rarity.replace("_", " ").title()

        slim_db[card_id] = {
            "name": card.get("name", "Unknown"),
            "number": str(collector_num),
            "rarity": rarity,
        }

    data_file = os.path.join(set_dir, "_data.json")
    _atomic_json_write(data_file, slim_db)

    # Download images
    download_count = 0
    skip_count = 0
    for card in cards:
        card_id = str(card.get("id", ""))
        if not card_id:
            continue

        # Lorcast nests images as image_uris.digital.{small,normal,large} (AVIF)
        # The "full" size (1468x2048) is JPG — construct it from the large URL
        image_uris = card.get("image_uris", {})
        digital = image_uris.get("digital", {}) if isinstance(image_uris, dict) else {}

        image_url = None
        # Prefer: construct full JPG URL from any available digital URL
        for size in ("large", "normal", "small"):
            avif_url = digital.get(size, "")
            if avif_url:
                # Replace /digital/{size}/ with /digital/full/ and .avif with .jpg
                image_url = avif_url.replace(f"/digital/{size}/", "/digital/full/").replace(".avif", ".jpg")
                # Strip query params for the extension swap, keep them for cache busting
                break

        # Fallback: use the AVIF URL directly (will work if Pillow has AVIF support)
        if not image_url:
            image_url = digital.get("large", digital.get("normal", ""))

        if not image_url:
            continue

        # Determine file extension from URL
        ext = ".jpg"
        if ".avif" in image_url.lower():
            ext = ".avif"
        elif ".png" in image_url.lower():
            ext = ".png"

        if not check_disk_space():
            print(f"\n     STOPPING: Less than {MIN_FREE_SPACE_MB}MB free space remaining.")
            return download_count, skip_count

        filepath = os.path.join(set_dir, f"{card_id}{ext}")
        status = download_file(image_url, filepath)

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
    print("=== Disney Lorcana Card Downloader (Lorcast API) ===\n")

    sets = fetch_sets()
    if not sets:
        print("No sets found to download.")
        return

    os.makedirs(BASE_DIR, exist_ok=True)

    # Build master_index.json
    master_index = {}
    for s in sets:
        code = s.get("code", s.get("id", "unknown"))
        released = s.get("released_at", s.get("release_date", ""))
        year = released[:4] if released else ""
        master_index[code] = {
            "name": s.get("name", code),
            "year": year,
        }

    index_path = os.path.join(BASE_DIR, "master_index.json")
    _atomic_json_write(index_path, master_index)
    print(f"2. Saved master_index.json ({len(master_index)} sets)\n")

    print("3. Downloading cards per set...")
    print("   Press CTRL+C to stop (you can resume later).\n")

    total_downloaded = 0
    total_skipped = 0

    for i, s in enumerate(sets):
        set_code = s.get("code", s.get("id", "unknown"))
        set_name = s.get("name", set_code)
        print(f"[{i + 1}/{len(sets)}] {set_name} ({set_code})")

        cards = fetch_cards_for_set(set_code)
        if not cards:
            print(f"     No cards found, skipping.")
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
